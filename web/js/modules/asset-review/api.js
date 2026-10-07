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

import assetReviewHttp from './http.js';

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
    const candidate = http || assetReviewHttp;
    if (!candidate || typeof candidate.request !== 'function') {
        throw new TypeError('An asset-review HTTP transport with request(url, init) is required');
    }
    return candidate;
}

function sameOriginInit(init = {}) {
    return {...init, credentials: 'same-origin'};
}

/**
 * Stateless boundary for authenticated review, delivery and sharing calls.
 * The page owns review context completion, response parsing and 401 UI state.
 */
export function createAssetReviewApi(http) {
    const transport = resolveHttp(http);
    const request = (url, init) => transport.request(url, init);

    return {
        getRegistryAsset(assetId, init = {}) {
            return request(`/api/asset-registry/assets/${encodeURIComponent(assetId)}`, sameOriginInit(init));
        },
        listReviewSessions(query = '', init = {}) {
            const suffix = String(query || '').replace(/^\?/, '');
            return request(`/api/asset-reviews/sessions${suffix ? `?${suffix}` : ''}`, sameOriginInit(init));
        },
        createReviewSession(payload, init = {}) {
            return request('/api/asset-reviews/sessions', jsonInit(payload, sameOriginInit({...init, method: 'POST'})));
        },
        getReviewSession(sessionId, init = {}) {
            return request(`/api/asset-reviews/sessions/${encodeURIComponent(sessionId)}`, sameOriginInit(init));
        },
        createReviewDelivery(sessionId, payload, init = {}) {
            return request(
                `/api/asset-reviews/sessions/${encodeURIComponent(sessionId)}/delivery`,
                jsonInit(payload, sameOriginInit({...init, method: 'POST'})),
            );
        },
        exportReviewDelivery(deliveryId, init = {}) {
            return request(
                `/api/asset-reviews/deliveries/${encodeURIComponent(deliveryId)}/export`,
                sameOriginInit({...init, method: 'POST'}),
            );
        },
        createReviewComment(sessionId, payload, init = {}) {
            return request(
                `/api/asset-reviews/sessions/${encodeURIComponent(sessionId)}/comments`,
                jsonInit(payload, sameOriginInit({...init, method: 'POST'})),
            );
        },
        updateReviewApproval(sessionId, payload, init = {}) {
            return request(
                `/api/asset-reviews/sessions/${encodeURIComponent(sessionId)}/approval`,
                jsonInit(payload, sameOriginInit({...init, method: 'PUT'})),
            );
        },
        createReviewShare(payload, init = {}) {
            return request('/api/asset-reviews/shares', jsonInit(payload, sameOriginInit({...init, method: 'POST'})));
        },
        updateReviewComment(commentId, payload, init = {}) {
            return request(
                `/api/asset-reviews/comments/${encodeURIComponent(commentId)}`,
                jsonInit(payload, sameOriginInit({...init, method: 'PATCH'})),
            );
        },
    };
}

const assetReviewApi = Object.freeze(createAssetReviewApi());
globalThis.GodsWorkbenchAssetReviewApi = assetReviewApi;

export default assetReviewApi;
