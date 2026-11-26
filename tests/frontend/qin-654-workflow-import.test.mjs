import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {File as NodeFile} from 'node:buffer';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const controller = readFileSync(new URL('../../web/js/controllers/workflow-controller.js', import.meta.url), 'utf8');
const importHelpers = controller.slice(controller.indexOf('function workflowAssetImportPrincipalKey()'), controller.indexOf('window.WorkbenchWorkflow ='));
const apiFetchSource = controller.slice(controller.indexOf('async function apiFetch('), controller.indexOf('/* ==================== 1. 初始化'));
const uploadSource = controller.slice(controller.indexOf('async function handleUploadFile('), controller.indexOf('async function offerDefinitionResolution('));

const PRINCIPAL_KEY = JSON.stringify(['local_account', 'qin-user']);
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
const WEBP = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]);

function response({status = 200, bytes = PNG, workflow = null} = {}) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: {get: name => name.toLowerCase() === 'content-length' && bytes ? String(bytes.length) : null},
        blob: async () => new Blob([bytes], {type: 'application/octet-stream'}),
        json: async () => workflow || {workflow_id: 'wf-imported', name: 'Imported', nodes: [{id: '1'}]},
    };
}

function harness({assetBytes = PNG, parseBehavior = null} = {}) {
    const storage = new Map();
    const toasts = [];
    const calls = [];
    const location = {href: 'https://workbench.test/static/pages/workflow.html?id=old&keep=1&import_asset_id=asset-full&import_nonce=01234567-89ab-cdef-0123-456789abcdef', origin: 'https://workbench.test'};
    const history = {state: {entry: true}, replaceState(_state, _title, next) { location.href = new URL(next, location.origin).href; }};
    const lifecycle = {signal: new AbortController().signal};
    const h = {
        window: null,
        document: {},
        location,
        history,
        URL,
        Blob,
        File: NodeFile,
        FormData,
        AbortController,
        fetch: async (url, options = {}) => {
            calls.push({url: String(url), method: String(options.method || 'GET').toUpperCase(), options});
            if (String(url).includes('/api/asset-registry/assets/')) return response({bytes: assetBytes});
            if (parseBehavior) return parseBehavior(url, options, calls);
            return response({workflow: {workflow_id: 'wf-imported', name: 'Imported', nodes: [{id: '1'}]}});
        },
        sessionStorage: {
            getItem: key => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, String(value)),
            removeItem: key => storage.delete(key),
        },
        ASSET_WORKFLOW_IMPORT_STORAGE_KEY: 'gw.workflow.assetImport.pending.v1',
        ASSET_WORKFLOW_IMPORT_CONSUMED_STORAGE_KEY: 'gw.workflow.assetImport.consumed.v1',
        ASSET_WORKFLOW_IMPORT_MAX_AGE_MS: 5 * 60 * 1000,
        ASSET_WORKFLOW_IMPORT_MAX_BYTES: 4 * 1024 * 1024,
        assetWorkflowImportInFlight: null,
        assetWorkflowImportSelectedWorkflowId: '',
        state: {
            authPrincipalKey: PRINCIPAL_KEY,
            activeFolderId: 'parsed_default',
            collapsedFolders: new Set(),
            workflows: [],
            currentWorkflow: null,
            pendingDefinitionWorkflow: null,
        },
        lifecycleAbortController: lifecycle,
        GWAuthGate: {revision: 7, isAuthenticated: () => true, invalidate() {}},
        HardwareDeck: {authState: {authenticated: true, auth_mode: 'local_account', principal: {subject: 'qin-user'}}},
        showToast: (...args) => toasts.push(args),
        executionSourceContext: () => ({}),
        normalizeMatureWorkflow: value => value,
        targetApiUrl: value => value,
        refreshWorkflowList: async () => {},
        setCurrentWorkflow: workflow => { h.state.currentWorkflow = workflow; },
        offerDefinitionResolution: async () => {},
    };
    h.window = h;
    h.history.state = {entry: true};
    h.state.authPrincipalKey = PRINCIPAL_KEY;
    h.state.collapsedFolders = new Set();
    h.sessionStorage.setItem('gw.workflow.assetImport.pending.v1', JSON.stringify({
        nonce: '01234567-89ab-cdef-0123-456789abcdef',
        asset_id: 'asset-full',
        principal_key: PRINCIPAL_KEY,
        expires_at: Date.now() + 60_000,
    }));
    runInNewContext(apiFetchSource + importHelpers + uploadSource, h);
    return {h, calls, storage, toasts, lifecycle};
}

test('QIN-654：原始 PNG/WebP 读取后复用 parse-upload，成功选中新建工作流并清理参数', async () => {
    for (const [format, bytes, extension] of [['PNG', PNG, '.png'], ['WebP', WEBP, '.webp']]) {
        const {h, calls, storage, toasts, lifecycle} = harness({assetBytes: bytes});
        const result = await h.consumeComfyAssetWorkflowImport(lifecycle);
        assert.equal(result.status, 'success', `${format} 应成功导入: ${JSON.stringify({calls: calls.map(call => [call.method, call.url]), toasts})}`);
        assert.equal(h.state.currentWorkflow.workflow_id, 'wf-imported');
        assert.deepEqual(calls.map(call => call.method), ['GET', 'POST']);
        assert.match(calls[0].url, /\/api\/asset-registry\/assets\/asset-full\/media$/);
        assert.equal(calls[0].options.credentials, 'same-origin');
        const form = calls[1].options.body;
        const uploaded = form.get('file');
        assert.ok(uploaded.name.endsWith(extension));
        assert.equal(uploaded.size, bytes.length);
        assert.equal(calls[1].url, '/api/god_workflow/parse-upload');
        assert.equal(calls[1].options.credentials, 'same-origin');
        assert.equal(storage.has('gw.workflow.assetImport.pending.v1'), false);
        assert.deepEqual(Array.from(JSON.parse(storage.get('gw.workflow.assetImport.consumed.v1'))), ['01234567-89ab-cdef-0123-456789abcdef']);
        const after = new URL(h.location.href);
        assert.equal(after.searchParams.get('id'), 'old');
        assert.equal(after.searchParams.get('keep'), '1');
        assert.equal(after.searchParams.has('import_asset_id'), false);
        assert.equal(after.searchParams.has('import_nonce'), false);
        const repeated = await h.consumeComfyAssetWorkflowImport(lifecycle);
        assert.equal(repeated.handled, false);
        assert.equal(calls.length, 2, '刷新/重入不得重复创建');
    }
});

test('QIN-654：无效 nonce、超过 4 MiB 的原图和认证切换均不调用创建接口', async () => {
    const invalid = harness();
    invalid.h.sessionStorage.setItem('gw.workflow.assetImport.pending.v1', JSON.stringify({
        nonce: '01234567-89ab-cdef-0123-456789abcdef', asset_id: 'another-asset', principal_key: PRINCIPAL_KEY, expires_at: Date.now() + 60_000,
    }));
    assert.equal((await invalid.h.consumeComfyAssetWorkflowImport(invalid.lifecycle)).status, 'failed');
    assert.equal(invalid.calls.length, 0);

    const oversized = harness({assetBytes: new Uint8Array(4 * 1024 * 1024 + 1)});
    assert.equal((await oversized.h.consumeComfyAssetWorkflowImport(oversized.lifecycle)).status, 'failed');
    assert.deepEqual(oversized.calls.map(call => call.method), ['GET'], JSON.stringify(oversized.toasts));

    const switched = harness();
    switched.h.fetch = async url => {
        switched.calls.push({url: String(url), method: 'GET', options: {}});
        switched.h.state.authPrincipalKey = JSON.stringify(['local_account', 'other-user']);
        return response();
    };
    assert.equal((await switched.h.consumeComfyAssetWorkflowImport(switched.lifecycle)).status, 'cancelled');
    assert.equal(switched.calls.length, 1, '身份变化后不能提交 parse-upload');
});

test('QIN-654：提交网络结果未知时提示检查列表，消费 nonce 且不自动重试', async () => {
    const {h, calls, toasts, lifecycle} = harness({parseBehavior: async () => { throw new TypeError('network lost'); }});
    const result = await h.consumeComfyAssetWorkflowImport(lifecycle);
    assert.equal(result.status, 'unknown');
    assert.ok(toasts.some(([message]) => /检查工作流列表/.test(message)));
    const repeated = await h.consumeComfyAssetWorkflowImport(lifecycle);
    assert.equal(repeated.handled, false);
    assert.equal(calls.length, 2);
});

test('QIN-654：initWorkbench 在有效导入时跳过默认选择；失败回退，rebind 优先保留导入项', async () => {
    const initSource = controller.slice(controller.indexOf('async function initWorkbench()'), controller.indexOf('// 结果未知任务：'));
    const selectionSource = controller.slice(controller.indexOf('async function selectInitialWorkflow('), controller.indexOf('window.WorkbenchWorkflow ='));
    function makeInitHarness(importResult) {
        const root = {};
        const selected = [];
        const h = {
            window: {GWAuthGate: {isAuthenticated: () => true}, location: {href: 'https://workbench.test/static/pages/workflow.html'}},
            document: {getElementById: id => id === 'workflowWorkbench' ? root : null},
            URL,
            state: {authLoading: false, authPrincipalKey: PRINCIPAL_KEY, workflows: [{workflow_id: 'default'}], currentWorkflow: null},
            workbenchRoot: root,
            lifecycleAbortController: {},
            assetWorkflowImportSelectedWorkflowId: '',
            renderProjectContextBadge() {},
            loadSettingsState: async () => {},
            refreshMediaAssetsCache: async () => {},
            refreshWorkflowList: async () => {},
            consumeComfyAssetWorkflowImport: async () => importResult(),
            selectWorkflow: async id => { selected.push(id); h.state.currentWorkflow = {workflow_id: id}; },
            restoreWorkflowTask: async () => {},
            startComfySyncWatcher() {},
            disposeWorkbench() {},
        };
        h.window.document = h.document;
        runInNewContext(initSource + '\n' + selectionSource + '\nthis.initWorkbench = initWorkbench;', h);
        return {h, selected};
    }

    const successful = makeInitHarness(async () => ({status: 'success'}));
    await successful.h.initWorkbench();
    assert.deepEqual(successful.selected, [], '导入选中新工作流后不应再选择默认项');

    const failed = makeInitHarness(async () => ({status: 'failed'}));
    await failed.h.initWorkbench();
    assert.deepEqual(failed.selected, ['default'], '导入失败且当前为空时应安全回退');

    const rebound = makeInitHarness(async () => ({status: 'none'}));
    rebound.h.assetWorkflowImportSelectedWorkflowId = 'wf-imported';
    rebound.h.state.workflows.push({workflow_id: 'wf-imported'});
    await rebound.h.initWorkbench();
    assert.deepEqual(rebound.selected, ['wf-imported'], 'rebind 应继续选择导入结果');
});
