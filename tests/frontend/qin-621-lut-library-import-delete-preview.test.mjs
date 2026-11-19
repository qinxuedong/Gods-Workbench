import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createAssetViewHost, wait } from './qin-602-asset-view-harness.mjs';

function trackUnrefTimers() {
    const prevSetInterval = globalThis.setInterval;
    const prevSetTimeout = globalThis.setTimeout;
    const intervals = new Set();
    const timeouts = new Set();
    globalThis.setInterval = (...args) => {
        const id = prevSetInterval(...args);
        id?.unref?.();
        intervals.add(id);
        return id;
    };
    globalThis.setTimeout = (...args) => {
        const id = prevSetTimeout(...args);
        timeouts.add(id);
        return id;
    };
    return () => {
        for (const id of intervals) clearInterval(id);
        for (const id of timeouts) clearTimeout(id);
        globalThis.setInterval = prevSetInterval;
        globalThis.setTimeout = prevSetTimeout;
    };
}

function okJson(payload) {
    return {
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        text: async () => JSON.stringify(payload),
        json: async () => payload,
    };
}

function patchScopeSelector(host) {
    const proto = Object.getPrototypeOf(host.root);
    const orig = proto.querySelectorAll;
    proto.querySelectorAll = function patchedQsa(selector) {
        if (typeof selector === 'string' && selector.startsWith(':scope > ')) {
            const sub = selector.slice(':scope > '.length);
            return (this.children || []).filter(child => child.matches?.(sub) || (sub.includes('.registry-card') && child.classList?.contains('registry-card')));
        }
        return orig.call(this, selector);
    };
    return () => { proto.querySelectorAll = orig; };
}

function makeLutItem(id, name, builtin = false) {
    return {
        id,
        asset_id: id,
        name,
        kind: 'lut',
        builtin,
        can_recycle: true,
        metadata: {
            extension: 'cube',
            ...(builtin ? { builtin: true } : { user_lut: true }),
            lut: { title: name, lut_3d_size: 8, sample_count: 512 },
        },
        display_path: builtin ? `registry_uploads/builtin_luts/${name}` : `registry_uploads/user-lut/${name}`,
        version: 1,
    };
}

test('QIN-621：同页重开回收站不会作废在途恢复，切页仍作废旧响应', async () => {
    const restoreTimers = trackUnrefTimers();
    let finishRestore;
    let loads = 0;
    const entry = {entry_id:'recycle_1', kind:'asset', payload:{name:'grade.cube', kind:'lut'}};
    const host = await createAssetViewHost({
        tab:'registry',
        fetchImpl: async url => {
            if (url === '/api/asset-registry/recycle-bin') {
                loads += 1;
                return okJson({items:[entry]});
            }
            if (url.endsWith('/recycle-bin/recycle_1/restore')) {
                return new Promise(resolve => { finishRestore = resolve; });
            }
            if (url === '/api/asset-registry/status') return okJson({revision:1});
            return okJson({});
        },
    });
    try {
        await host.main.gwCall('organize', 'activateAssetBoard', 'lut');
        await host.main.gwCall('recycle', 'openRegistryRecycleBin');
        const { restoreRegistryRecycleEntry: restore } = await import(pathToFileURL(join(
            host.tree, 'web/js/modules/asset-manager/recycle.js',
        )).href);
        const overlay = () => host.document.getElementById('registryRecycleBinOverlay');
        const success = restore('recycle_1');
        await host.main.gwCall('recycle', 'openRegistryRecycleBin');
        assert.equal(loads, 2);
        finishRestore(okJson({restored:{name:'grade.cube'}}));
        await success;
        assert.match(overlay().innerHTML, /回收站暂无素材/, '同页读回后，恢复成功仍应移除已恢复条目');

        await host.main.gwCall('recycle', 'openRegistryRecycleBin');
        const failure = restore('recycle_1');
        await host.main.gwCall('recycle', 'openRegistryRecycleBin');
        finishRestore({...okJson({detail:{message:'恢复请求失败'}}), ok:false, status:409});
        await failure;
        assert.match(overlay().innerHTML, /重新加载/);
        await host.main.gwCall('recycle', 'openRegistryRecycleBin');
        assert.doesNotMatch(overlay().innerHTML, /data-registry-recycle-restore="recycle_1" disabled/);

        const stale = restore('recycle_1');
        await host.main.gwCall('organize', 'activateAssetBoard', 'registry');
        finishRestore(okJson({restored:{name:'grade.cube'}}));
        await stale;
        assert.ok(!host.document.body.querySelector('[id="registryRecycleBinOverlay"]'), '切页后不应重开回收站');
    } finally {
        host.dispose();
        restoreTimers();
    }
});

test('QIN-621 AC4：自定义样片优先进入绘制流程，加载失败才回落默认图', async () => {
    const restoreTimers = trackUnrefTimers();
    const previousImage = globalThis.Image;
    const lutFile = 'web/js/modules/asset-manager/lut.js';
    const host = await createAssetViewHost({
        tab:null,
        fileEdits:[{file:lutFile, append:'export { loadLutDefaultPreviewFrame };'}],
    });
    const requested = [];
    globalThis.Image = class {
        set src(url) {
            requested.push(url);
            this.url = url;
            queueMicrotask(() => url.includes('missing') ? this.onerror() : this.onload());
        }
    };
    const createElement = host.document.createElement.bind(host.document);
    host.document.createElement = tag => {
        if (tag !== 'canvas') return createElement(tag);
        let value = 0;
        return { getContext: () => ({
            fillRect() {},
            drawImage(image) { value = image.url.includes('custom.png') ? 213 : 42; },
            getImageData() { return {data:new Uint8ClampedArray([value, 0, 0, 255])}; },
        }) };
    };
    try {
        const { loadLutDefaultPreviewFrame: sample } = await import(pathToFileURL(join(host.tree, lutFile)).href);
        assert.equal((await sample(1, 1, '/custom.png')).data[0], 213);
        assert.deepEqual(requested, ['/custom.png']);
        assert.equal((await sample(1, 1, '/missing.png')).data[0], 42);
        assert.deepEqual(requested.slice(1), ['/missing.png', '/static/vendor/luts/default-preview.png']);
    } finally {
        globalThis.Image = previousImage;
        host.dispose();
        restoreTimers();
    }
});

test('QIN-621 AC1：资产总量查询携带 category=image,video，独立 LUT 看板携带 kind=lut', async () => {
    const restoreTimers = trackUnrefTimers();
    const host = await createAssetViewHost({ tab: 'registry' });
    try {
        const assetRequests = host.requests.filter(r => r.url.startsWith('/api/asset-registry/assets'));
        assert.ok(assetRequests.length > 0, '进入资产总量应发起列表查询');
        assert.ok(
            assetRequests.some(r => r.url.includes('category=image%2Cvideo') || r.url.includes('category=image,video')),
            `资产总量请求应携带 category=image,video，实际：${assetRequests.map(r => r.url).join('; ')}`,
        );

        host.requests.length = 0;
        await host.main.gwCall('organize', 'activateAssetBoard', 'lut');
        await wait(60);
        const lutRequests = host.requests.filter(r => r.url.startsWith('/api/asset-registry/assets'));
        assert.ok(
            lutRequests.some(r => r.url.includes('kind=lut')),
            `LUT 看板应携带 kind=lut，实际：${lutRequests.map(r => r.url).join('; ')}`,
        );
    } finally {
        host.dispose();
        restoreTimers();
    }
});

test('QIN-621 AC2：LUT 库内置与用户卡片统一渲染删除按钮，无隐藏与恢复显示入口', async () => {
    const restoreTimers = trackUnrefTimers();
    const lutItems = [
        makeLutItem('builtin_lut_neutral', 'builtin_lut_neutral.cube', true),
        makeLutItem('ast_user_lut_1', 'my_film.cube', false),
    ];
    lutItems[1].metadata.lut.preview_url = '/static/fixture/custom-preview.png';
    const host = await createAssetViewHost({
        tab: 'registry',
        fetchImpl: async (url) => {
            if (String(url).startsWith('/api/asset-registry/assets?') && String(url).includes('kind=lut')) {
                return okJson({
                    items: lutItems,
                    assets: lutItems,
                    total: lutItems.length,
                    has_more: false,
                    revision: 1,
                });
            }
            return okJson({});
        },
    });
    const restoreQsa = patchScopeSelector(host);
    try {
        await host.main.gwCall('organize', 'activateAssetBoard', 'lut');
        await wait(80);
        const html = host.root.innerHTML;
        assert.ok(!html.includes('registry-card-builtin-hide'), '不应再渲染内置隐藏按钮');
        assert.ok(!html.includes('data-registry-reveal-builtin'), '不应再渲染恢复显示入口');
        const recycleButtons = host.root.querySelectorAll('[data-registry-recycle]');
        assert.equal(recycleButtons.length, 2, '内置与用户 LUT 卡片都应渲染删除按钮');
        assert.ok(!recycleButtons[0].hasAttribute('disabled') && !recycleButtons[1].hasAttribute('disabled'), '受管 LUT 删除按钮应可点击');
        const previews = [...host.root.querySelectorAll('[data-lut-custom-preview-url]')];
        assert.deepEqual(previews.map(node => node.getAttribute('data-lut-custom-preview-url')), [
            '', '/static/fixture/custom-preview.png',
        ]);
    } finally {
        restoreQsa();
        host.dispose();
        restoreTimers();
    }
});

test('QIN-621 AC3 & AC6：LUT 库渲染「导入 LUT」与拖入热区，验证慢请求(1500ms)、拒绝非.cube、切页取消与写后刷新失败', async () => {
    const restoreTimers = trackUnrefTimers();
    let importDelay = 0;
    let listShouldFail = false;
    let currentItems = [makeLutItem('builtin_lut_neutral', 'builtin_lut_neutral.cube', true)];
    const host = await createAssetViewHost({
        tab: 'registry',
        fetchImpl: async (url, init = {}) => {
            const target = String(url);
            if (target.startsWith('/api/asset-registry/assets/import')) {
                if (importDelay > 0) {
                    await new Promise((resolve, reject) => {
                        const timer = setTimeout(resolve, importDelay);
                        init?.signal?.addEventListener?.('abort', () => {
                            clearTimeout(timer);
                            const err = new Error('Aborted');
                            err.name = 'AbortError';
                            reject(err);
                        });
                    });
                }
                const newLut = makeLutItem('ast_new_1', 'slow_import.cube', false);
                currentItems = [...currentItems, newLut];
                return okJson({ asset: newLut, revision: 2 });
            }
            if (target.startsWith('/api/asset-registry/assets?') && target.includes('kind=lut')) {
                if (listShouldFail) {
                    const errPayload = { detail: { message: '刷新超时' } };
                    return {
                        ok: false,
                        status: 500,
                        headers: { get: () => 'application/json' },
                        text: async () => JSON.stringify(errPayload),
                        json: async () => errPayload,
                    };
                }
                return okJson({
                    items: currentItems,
                    assets: currentItems,
                    total: currentItems.length,
                    has_more: false,
                    revision: 2,
                });
            }
            return okJson({});
        },
    });
    const restoreQsa = patchScopeSelector(host);
    try {
        await host.main.gwCall('organize', 'activateAssetBoard', 'lut');
        await wait(80);
        assert.ok(host.root.querySelector('[data-lut-import-trigger]'), '应渲染导入 LUT 按钮');
        assert.ok(host.root.querySelector('[data-lut-dropzone]'), '应挂载 LUT 页面拖入热区');

        const makeCubeFile = (name, content = 'LUT_3D_SIZE 2\n') => {
            const blob = new Blob([content], { type: 'text/plain' });
            Object.defineProperty(blob, 'name', { value: name });
            return blob;
        };

        // 1. 拒绝非 .cube
        await host.main.gwCall('lut', 'importLutFiles', [makeCubeFile('invalid.3dl', 'bad')]);
        assert.ok(host.root.innerHTML.includes('仅支持导入 .cube'), '非 .cube 必须显示明确失败');

        // 2. 1500ms 慢请求期间锁定按钮、不得提前标「已导入」
        importDelay = 1500;
        const slowPromise = host.main.gwCall('lut', 'importLutFiles', [makeCubeFile('slow_import.cube')]);
        await wait(30);
        const busyBtn = host.root.querySelector('[data-lut-import-trigger]');
        assert.ok(busyBtn?.hasAttribute('disabled') || busyBtn?.getAttribute('aria-busy') === 'true', '慢请求期间导入按钮应锁定');
        assert.ok(!host.root.innerHTML.includes('已导入 1 个 LUT'), '慢请求未完成不得提前标已导入');
        await slowPromise;
        assert.ok(host.root.innerHTML.includes('已导入 1 个 LUT'), '慢请求完成后应显示已导入');

        // 3. 切离 LUT 页取消慢请求（旧请求不得写入状态）
        importDelay = 1500;
        const abortedPromise = host.main.gwCall('lut', 'importLutFiles', [makeCubeFile('cancelled.cube')]);
        await wait(30);
        await host.main.gwCall('organize', 'activateAssetBoard', 'registry');
        await abortedPromise;
        assert.ok(!host.root.innerHTML.includes('已导入 1 个 LUT'), '切离 LUT 页后旧响应不得写入');
    } finally {
        restoreQsa();
        host.dispose();
        restoreTimers();
    }
});

test('QIN-621 AC6：写后刷新失败降级，保留已导入成功提示并提供重试', async () => {
    const restoreTimers = trackUnrefTimers();
    let listShouldFail = false;
    let currentItems = [makeLutItem('builtin_lut_neutral', 'builtin_lut_neutral.cube', true)];
    const host = await createAssetViewHost({
        tab: 'registry',
        fetchImpl: async (url) => {
            const target = String(url);
            if (target.startsWith('/api/asset-registry/assets/import')) {
                const newLut = makeLutItem('ast_new_2', 'after_fail.cube', false);
                currentItems = [...currentItems, newLut];
                return okJson({ asset: newLut, revision: 2 });
            }
            if (target.startsWith('/api/asset-registry/assets?') && target.includes('kind=lut')) {
                if (listShouldFail) {
                    const errPayload = { detail: { message: '刷新超时' } };
                    return {
                        ok: false,
                        status: 500,
                        headers: { get: () => 'application/json' },
                        text: async () => JSON.stringify(errPayload),
                        json: async () => errPayload,
                    };
                }
                return okJson({
                    items: currentItems,
                    assets: currentItems,
                    total: currentItems.length,
                    has_more: false,
                    revision: 2,
                });
            }
            return okJson({});
        },
    });
    const restoreQsa = patchScopeSelector(host);
    try {
        await host.main.gwCall('organize', 'activateAssetBoard', 'lut');
        await wait(80);
        listShouldFail = true;
        const blob = new Blob(['LUT_3D_SIZE 2\n'], { type: 'text/plain' });
        Object.defineProperty(blob, 'name', { value: 'after_fail.cube' });
        await host.main.gwCall('lut', 'importLutFiles', [blob]);
        assert.ok(host.root.innerHTML.includes('已导入 1 个 LUT'), '写后刷新失败不得否定已成功写入');
        assert.ok(host.root.querySelector('[data-lut-async-retry]'), '写后刷新失败应提供重试按钮');
    } finally {
        restoreQsa();
        host.dispose();
        restoreTimers();
    }
});

test('QIN-621 AC4 & AC5：默认预览加载静态图片路径且样式覆盖双主题/四宽度防溢出规则', async () => {
    const lutSource = readFileSync(new URL('../../web/js/modules/asset-manager/lut.js', import.meta.url), 'utf8');
    const cssSource = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
    assert.ok(
        lutSource.includes('/static/vendor/luts/default-preview.png'),
        'lut.js 应使用 /static/vendor/luts/default-preview.png 作为默认预览图',
    );
    assert.match(cssSource, /\.registry-lut-import-btn\s*\{[^}]*flex-shrink:\s*0;/);
    assert.match(cssSource, /\.registry-lut-conflict-modal\s+\.asset-modal\s*\{[^}]*max-width:\s*min\(460px/);
});
