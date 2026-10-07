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

/**
 * Shared stateless fetch transports used by domain-specific API facades.
 *
 * 统一「无后端时显式降级」：当后端**根本没有该路由**（FastAPI 默认 404，
 * 响应体不含标准错误包 `detail.code`）或显式返回 501 时，转换为
 * `unavailable=true`、`code=NOT_INTEGRATED` 的显式错误，页面据此渲染
 * 「未接入」提示或禁用态，而不是静默失败。
 * 已实现接口的真实错误（含业务 404，如 `CANVAS_NOT_FOUND`、`PROJECT_NOT_FOUND`，
 * 以及 400/401/403/409/其他 5xx）一律原样透传；**503 单独归类为「服务暂时不可用」**
 * （可恢复，错误码 `SERVICE_UNAVAILABLE`），不得报成「未纳入当前切片」。
 *
 * The transports intentionally return native fetch responses untouched. JSON,
 * text and blob parsing belongs to each page/API boundary; response error
 * projection is shared because it has no domain state.
 */
export const NOT_INTEGRATED_MESSAGE = '该功能尚未接入后端（未纳入当前切片）';

/** 只有「无标准错误包」的 404/501 才视为后端未接入；503 属可恢复的服务不可用。 */
const NOT_INTEGRATED_STATUSES = new Set([404, 501]);

/** 503 表示后端服务**暂时不可用**（可恢复），不得与「未纳入当前切片」混淆。 */
export const SERVICE_UNAVAILABLE_MESSAGE = '后端服务暂时不可用，请稍后重试';

/** 构造显式降级错误，携带结构化标记供页面判定。 */
export function createNotIntegratedError(status) {
    const error = new Error(`${NOT_INTEGRATED_MESSAGE}（HTTP ${status}）`);
    error.name = 'NotIntegratedError';
    error.unavailable = true;
    error.code = 'NOT_INTEGRATED';
    error.status = status;
    return error;
}

/** 构造可恢复「服务暂时不可用」错误（503），与未接入降级严格区分。 */
export function createServiceUnavailableError(status) {
    const error = new Error(`${SERVICE_UNAVAILABLE_MESSAGE}（HTTP ${status}）`);
    error.name = 'ServiceUnavailableError';
    error.unavailable = false;
    error.retryable = true;
    error.code = 'SERVICE_UNAVAILABLE';
    error.status = status;
    return error;
}

/** 判定错误是否为可恢复的服务不可用错误。 */
export function isServiceUnavailableError(error) {
    return Boolean(error && error.code === 'SERVICE_UNAVAILABLE');
}

/** 判定错误是否为显式降级错误。 */
export function isNotIntegratedError(error) {
    return Boolean(error && error.code === 'NOT_INTEGRATED');
}

/**
 * 判定响应是否代表「路由不存在」。
 * 先看状态码，再读取响应副本：若含标准错误包 `detail.code`，说明路由存在，
 * 属真实业务错误，必须原样透传；否则判为未接入。
 */
export async function isNotIntegratedResponse(response) {
    if (!response || !NOT_INTEGRATED_STATUSES.has(response.status)) return false;
    let data = null;
    try {
        data = await response.clone().json();
    } catch (_) {
        // 非 JSON（例如反向代理返回 HTML 错误页）：视为路由未接入。
        return true;
    }
    const detail = data && data.detail;
    // 标准错误包（对象 detail）：路由存在，属真实业务错误，必须原样透传。
    if (detail && typeof detail === 'object') return false;
    // FastAPI 默认 404/501 的字符串 detail：路由不存在，属未接入。
    if (typeof detail === 'string' && detail.trim() && !/^(not found|not implemented)$/i.test(detail.trim())) {
        return false;
    }
    return true;
}

export function createFetchTransport(fetchImpl = globalThis.fetch) {
    if (typeof fetchImpl !== 'function') {
        throw new TypeError('A fetch implementation is required');
    }

    return {
        async request(url, init) {
            const response = await fetchImpl(url, init);
            if (await isNotIntegratedResponse(response)) {
                throw createNotIntegratedError(response.status);
            }
            if (response.status === 503) {
                throw createServiceUnavailableError(response.status);
            }
            return response;
        },
    };
}

/**
 * Legacy canvas-list keeps global fetch instrumentation observable when it is
 * installed after module evaluation, so its default transport resolves lazily.
 */
export function createLazyFetchTransport(fetchImpl) {
    if (fetchImpl !== undefined && typeof fetchImpl !== 'function') {
        throw new TypeError('A fetch implementation is required');
    }

    return {
        request(url, init) {
            const activeFetch = fetchImpl || globalThis.fetch;
            if (typeof activeFetch !== 'function') {
                throw new TypeError('A fetch implementation is required');
            }
            return activeFetch.call(globalThis, url, init).then(async response => {
                if (await isNotIntegratedResponse(response)) {
                    throw createNotIntegratedError(response.status);
                }
                if (response.status === 503) {
                    throw createServiceUnavailableError(response.status);
                }
                return response;
            });
        },
    };
}

/**
 * Convert the response shapes emitted by FastAPI and upstream providers into
 * a user-facing message without consuming the response body more than once.
 */
export function apiErrorMessage(data, fallback = '请求失败') {
    if (!data) return fallback;
    if (typeof data === 'string') return data || fallback;
    const detail = data.detail ?? data.error ?? data.message;
    if (typeof detail === 'string') return detail || fallback;
    if (Array.isArray(detail)) {
        const messages = detail.map(item => {
            if (typeof item === 'string') return item;
            const loc = Array.isArray(item?.loc) ? item.loc.filter(x => x !== 'body').join('.') : '';
            const msg = item?.msg || item?.message || JSON.stringify(item);
            return loc ? `${loc}: ${msg}` : msg;
        }).filter(Boolean);
        return messages.join('\n') || fallback;
    }
    if (detail && typeof detail === 'object') {
        return detail.message || detail.msg || JSON.stringify(detail);
    }
    try {
        return JSON.stringify(data);
    } catch (_) {
        return fallback;
    }
}

export async function responseErrorMessage(response, fallback = '请求失败') {
    try {
        const data = await response.clone().json();
        return apiErrorMessage(data, fallback);
    } catch (_) {
        try {
            const text = await response.text();
            return text || fallback;
        } catch (_) {
            return fallback;
        }
    }
}

// Legacy script entries cannot import an ES module, so expose only this
// stateless helper bundle while keeping their existing local function names.
globalThis.GodsWorkbenchHttpErrors = Object.freeze({
    apiErrorMessage,
    responseErrorMessage,
    NOT_INTEGRATED_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    createNotIntegratedError,
    isNotIntegratedError,
    isNotIntegratedResponse,
    createServiceUnavailableError,
    isServiceUnavailableError,
});
