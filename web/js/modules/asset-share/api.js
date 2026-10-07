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

import assetShareHttp from './http.js';

function jsonInit(payload, init = {}) {
    const sourceHeaders = init.headers;
    const useNativeHeaders = typeof Headers !== 'undefined'
        && (sourceHeaders instanceof Headers || Array.isArray(sourceHeaders));
    const headers = useNativeHeaders ? new Headers(sourceHeaders) : {...(sourceHeaders || {})};
    if (useNativeHeaders) {
        if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    } else if (!Object.keys(headers).some(name => name.toLowerCase() === 'content-type')) {
        headers['Content-Type'] = 'application/json';
    }
    return {...init, headers, body: JSON.stringify(payload)};
}

function resolveHttp(http) {
    const candidate = http || assetShareHttp;
    if (!candidate || typeof candidate.request !== 'function') {
        throw new TypeError('An asset-share HTTP transport with request(url, init) is required');
    }
    return candidate;
}

/**
 * Stateless boundary for public share access. It deliberately preserves the
 * caller's credential mode because public ticket access is not authenticated.
 */
export function createAssetShareApi(http) {
    const transport = resolveHttp(http);
    const request = (url, init) => transport.request(url, init);
    const shareUrl = token => `/api/public/shares/${encodeURIComponent(token)}`;

    return {
        getPublicShare(token, init) {
            return request(shareUrl(token), init);
        },
        accessPublicShare(token, payload, init = {}) {
            return request(`${shareUrl(token)}/access`, jsonInit(payload, {...init, method: 'POST'}));
        },
        createPublicShareComment(token, payload, init = {}) {
            return request(`${shareUrl(token)}/comments`, jsonInit(payload, {...init, method: 'POST'}));
        },
        updatePublicShareApproval(token, payload, init = {}) {
            return request(`${shareUrl(token)}/approvals`, jsonInit(payload, {...init, method: 'PUT'}));
        },
        getPublicShareMedia(token, assetId, ticket, download = false, init = {}) {
            if (typeof ticket !== 'string' || !ticket) throw new TypeError('A public share ticket is required');
            const base = `${shareUrl(token)}/assets/${encodeURIComponent(assetId)}/media`;
            const query = new URLSearchParams();
            if (download) query.set('download', 'true');
            const sourceHeaders = init.headers;
            const useNativeHeaders = typeof Headers !== 'undefined';
            const headers = useNativeHeaders
                ? new Headers(sourceHeaders || {})
                : {...(sourceHeaders || {})};
            if (useNativeHeaders) headers.set('X-Share-Ticket', ticket);
            else headers['X-Share-Ticket'] = ticket;
            const search = query.toString();
            return request(search ? `${base}?${search}` : base, {
                ...init,
                method: 'GET',
                headers,
                credentials: init.credentials || 'same-origin',
            });
        },
    };
}


/**
 * 有界的访客媒体加载器：同一分享页只保留当前预览 URL；切换/换票/离页时
 * Abort 全部在途读取并立即撤销所有 Blob URL。
 */
export function createAssetShareMediaSession(api, options = {}) {
    const mediaApi = api || assetShareApi;
    if (!mediaApi || typeof mediaApi.getPublicShareMedia !== 'function') {
        throw new TypeError('An asset-share API with getPublicShareMedia is required');
    }
    const urlApi = options.urlApi || globalThis.URL;
    const abortControllerFactory = options.abortControllerFactory || (() => new AbortController());
    const maxBytes = Number.isSafeInteger(options.maxBytes) && options.maxBytes > 0
        ? options.maxBytes
        : 64 * 1024 * 1024;
    if (!urlApi || typeof urlApi.createObjectURL !== 'function' || typeof urlApi.revokeObjectURL !== 'function') {
        throw new TypeError('A Blob URL API is required');
    }

    let contextGeneration = 0;
    let previewGeneration = 0;
    let previewController = null;
    let downloadController = null;
    let previewUrl = null;
    const controllers = new Set();
    const objectUrls = new Set();

    function revoke(url) {
        if (!url || !objectUrls.has(url)) return;
        objectUrls.delete(url);
        urlApi.revokeObjectURL(url);
        if (previewUrl === url) previewUrl = null;
    }

    function abort(controller) {
        if (controller && !controller.signal.aborted) controller.abort();
        if (controller) controllers.delete(controller);
    }

    function invalidate() {
        contextGeneration += 1;
        previewGeneration += 1;
        for (const controller of [...controllers]) abort(controller);
        previewController = null;
        downloadController = null;
        for (const url of [...objectUrls]) revoke(url);
    }

    async function responseError(response) {
        const data = await response.json().catch(() => ({}));
        const detail = data?.detail;
        const error = new Error(detail?.message || '无法读取分享媒体');
        error.name = 'AssetShareMediaError';
        error.status = response.status;
        error.code = detail?.code || 'SHARE_MEDIA_FAILED';
        return error;
    }

    async function readBoundedBlob(response, controller, isCurrent) {
        const rawLength = response.headers?.get?.('content-length');
        const declaredLength = rawLength ? Number(rawLength) : null;
        if (declaredLength !== null && (!Number.isFinite(declaredLength) || declaredLength < 0 || declaredLength > maxBytes)) {
            throw Object.assign(new Error('媒体超过本地预览大小上限'), {name: 'AssetShareMediaTooLargeError', status: 413});
        }
        const reader = response.body?.getReader?.();
        if (!reader) {
            if (declaredLength === null) {
                throw Object.assign(new Error('媒体缺少可核验长度，已停止读取'), {name: 'AssetShareMediaSizeUnknownError', status: 413});
            }
            const blob = await response.blob();
            if (blob.size > maxBytes) {
                throw Object.assign(new Error('媒体超过本地预览大小上限'), {name: 'AssetShareMediaTooLargeError', status: 413});
            }
            return blob;
        }
        const chunks = [];
        let size = 0;
        try {
            while (true) {
                if (!isCurrent() || controller.signal.aborted) {
                    await reader.cancel().catch(() => {});
                    return null;
                }
                const {done, value} = await reader.read();
                if (done) break;
                size += value?.byteLength || 0;
                if (size > maxBytes) {
                    await reader.cancel().catch(() => {});
                    throw Object.assign(new Error('媒体超过本地预览大小上限'), {name: 'AssetShareMediaTooLargeError', status: 413});
                }
                chunks.push(value);
            }
        } finally {
            try { reader.releaseLock(); } catch (_) {}
        }
        if (!isCurrent() || controller.signal.aborted) return null;
        return new Blob(chunks, {type: response.headers?.get?.('content-type') || ''});
    }

    async function fetchBlob(token, assetId, ticket, download, controller, isCurrent) {
        const response = await mediaApi.getPublicShareMedia(token, assetId, ticket, download, {
            signal: controller.signal,
            credentials: 'same-origin',
        });
        if (!response.ok) throw await responseError(response);
        return readBoundedBlob(response, controller, isCurrent);
    }

    function registerUrl(blob) {
        const url = urlApi.createObjectURL(blob);
        objectUrls.add(url);
        return url;
    }

    async function loadPreview(token, assetId, ticket) {
        abort(previewController);
        if (previewUrl) revoke(previewUrl);
        previewGeneration += 1;
        const localPreview = previewGeneration;
        const localContext = contextGeneration;
        const controller = abortControllerFactory();
        previewController = controller;
        controllers.add(controller);
        const isCurrent = () => localContext === contextGeneration && localPreview === previewGeneration;
        try {
            const blob = await fetchBlob(token, assetId, ticket, false, controller, isCurrent);
            if (!blob || !isCurrent()) return null;
            previewUrl = registerUrl(blob);
            return previewUrl;
        } catch (error) {
            if (!isCurrent() || controller.signal.aborted) return null;
            throw error;
        } finally {
            controllers.delete(controller);
            if (previewController === controller) previewController = null;
        }
    }

    async function createDownload(token, assetId, ticket) {
        abort(downloadController);
        const localContext = contextGeneration;
        const controller = abortControllerFactory();
        downloadController = controller;
        controllers.add(controller);
        const isCurrent = () => localContext === contextGeneration;
        try {
            const blob = await fetchBlob(token, assetId, ticket, true, controller, isCurrent);
            if (!blob || !isCurrent()) return null;
            const url = registerUrl(blob);
            let disposed = false;
            return {
                url,
                dispose() {
                    if (disposed) return;
                    disposed = true;
                    revoke(url);
                },
            };
        } catch (error) {
            if (!isCurrent() || controller.signal.aborted) return null;
            throw error;
        } finally {
            controllers.delete(controller);
            if (downloadController === controller) downloadController = null;
        }
    }

    return Object.freeze({loadPreview, createDownload, invalidate, maxBytes});
}

const assetShareApi = Object.freeze(createAssetShareApi());

export default assetShareApi;
