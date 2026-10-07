import test from 'node:test';
import assert from 'node:assert/strict';
import {createAssetShareApi, createAssetShareMediaSession} from '../../web/js/modules/asset-share/api.js';

function recordingUrlApi() {
    let sequence = 0;
    const created = new Map();
    const revoked = [];
    return {
        created,
        revoked,
        api: {
            createObjectURL(blob) {
                const url = `blob:share-test/${++sequence}`;
                created.set(url, blob);
                return url;
            },
            revokeObjectURL(url) { revoked.push(url); },
        },
    };
}

function response(bytes, headers = {}) {
    return new Response(new Uint8Array(bytes), {status: 200, headers: {
        'Content-Type': 'image/png',
        'Content-Length': String(bytes.length),
        ...headers,
    }});
}

test('媒体 API 只传同源路径、X-Share-Ticket 和 URLSearchParams 下载标记', async () => {
    let captured;
    const api = createAssetShareApi({request: async (url, init) => {
        captured = {url, init};
        return response([1, 2, 3]);
    }});
    const result = await api.getPublicShareMedia('share-token', 'ast_0001', 'secret-ticket', true);
    assert.equal(result.status, 200);
    assert.equal(captured.url, '/api/public/shares/share-token/assets/ast_0001/media?download=true');
    assert.equal(captured.init.method, 'GET');
    assert.equal(captured.init.credentials, 'same-origin');
    assert.equal(captured.init.headers.get('X-Share-Ticket'), 'secret-ticket');
    assert.equal(captured.url.includes('secret-ticket'), false);
    assert.equal(captured.url.startsWith('http'), false);
});

test('预览 Blob 有界、下载独立释放，切换时撤销所有 URL', async () => {
    const urls = recordingUrlApi();
    const api = {getPublicShareMedia: async (_token, _asset, _ticket, download) =>
        response(download ? [9, 8] : [1, 2, 3])};
    const media = createAssetShareMediaSession(api, {urlApi: urls.api, maxBytes: 8});
    const previewUrl = await media.loadPreview('token', 'ast_0001', 'ticket');
    assert.equal(await urls.created.get(previewUrl).text(), '\u0001\u0002\u0003');
    const download = await media.createDownload('token', 'ast_0001', 'ticket');
    assert.ok(download.url);
    download.dispose();
    download.dispose();
    assert.equal(urls.revoked.filter(url => url === download.url).length, 1);
    media.invalidate();
    assert.ok(urls.revoked.includes(previewUrl));
    assert.equal(urls.created.size, 2);
});

test('未知长度的流超过上限会中止且不会创建 Blob URL', async () => {
    const urls = recordingUrlApi();
    const api = {getPublicShareMedia: async () => new Response(new ReadableStream({
        start(controller) {
            controller.enqueue(new Uint8Array([1, 2, 3]));
            controller.enqueue(new Uint8Array([4, 5, 6]));
            controller.close();
        },
    }), {status: 200, headers: {'Content-Type': 'video/mp4'}})};
    const media = createAssetShareMediaSession(api, {urlApi: urls.api, maxBytes: 5});
    await assert.rejects(media.loadPreview('token', 'ast_0001', 'ticket'), error =>
        error.name === 'AssetShareMediaTooLargeError' && error.status === 413);
    assert.equal(urls.created.size, 0);
});

test('失效/失败会 Abort 请求并阻止迟到响应重建已撤销 URL', async () => {
    const urls = recordingUrlApi();
    let resolveResponse;
    let seenSignal;
    const api = {getPublicShareMedia: (_token, _asset, _ticket, _download, init) => {
        seenSignal = init.signal;
        return new Promise(resolve => { resolveResponse = resolve; });
    }};
    const media = createAssetShareMediaSession(api, {urlApi: urls.api, maxBytes: 8});
    const loading = media.loadPreview('token', 'ast_0001', 'ticket');
    media.invalidate();
    assert.equal(seenSignal.aborted, true);
    resolveResponse(response([1, 2, 3]));
    assert.equal(await loading, null);
    assert.equal(urls.created.size, 0);

    let call = 0;
    const failingApi = {getPublicShareMedia: async () => {
        call += 1;
        return call === 1
            ? response([1, 2])
            : new Response(JSON.stringify({detail: {code: 'SHARE_TICKET_REQUIRED', message: 'invalid'}}), {
                status: 401,
                headers: {'Content-Type': 'application/json'},
            });
    }};
    const failedMedia = createAssetShareMediaSession(failingApi, {urlApi: urls.api, maxBytes: 8});
    const existing = await failedMedia.loadPreview('token', 'ast_0001', 'ticket');
    await assert.rejects(failedMedia.loadPreview('token', 'ast_0001', 'stale'), error =>
        error.status === 401 && error.code === 'SHARE_TICKET_REQUIRED');
    assert.ok(urls.revoked.includes(existing));
    failedMedia.invalidate();
});
