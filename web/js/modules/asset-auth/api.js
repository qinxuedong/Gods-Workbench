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

import assetAuthHttp from './http.js';

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
    const candidate = http || assetAuthHttp;
    if (!candidate || typeof candidate.request !== 'function') {
        throw new TypeError('An asset-auth HTTP transport with request(url, init) is required');
    }
    return candidate;
}

function sameOriginInit(init = {}) {
    return {...init, credentials: 'same-origin'};
}

/**
 * Stateless boundary for authenticated account, team and operation approval
 * calls. The page owns response parsing, 401 projection and UI state.
 */
export function createAssetAuthApi(http) {
    const transport = resolveHttp(http);
    const request = (url, init) => transport.request(url, init);
    const userUrl = userId => `/api/asset-auth/users/${encodeURIComponent(userId)}`;
    const teamUrl = teamId => `/api/asset-auth/teams/${encodeURIComponent(teamId)}`;

    return {
        getStatus(init = {}) {
            return request('/api/asset-auth/status', sameOriginInit(init));
        },
        /**
         * 本仓当前切片**未提供** /api/asset-auth/bootstrap（本地首位管理员初始化）。
         * 保留该 façade 仅为兼容既有调用方；调用方必须按未接入错误显式降级，
         * 不得把它当作可用的初始化入口（见 http-transport.js 的 NOT_INTEGRATED 语义）。
         */
        bootstrap(payload, init = {}) {
            return request('/api/asset-auth/bootstrap', jsonInit(payload, sameOriginInit({...init, method: 'POST'})));
        },
        /**
         * 发起外部 IdP 登录（OIDC Authorization Code + PKCE）。
         *
         * 该端点**不接收用户名/密码**：后端返回 { authorization_url, state }，
         * 调用方应把 authorization_url 交给浏览器跳转（见 hardware-telemetry.js）。
         * 凭据只在 IdP 页面输入，前端不得收集或转发。
         */
        login(init = {}) {
            return request('/api/asset-auth/login', sameOriginInit({...init, method: 'POST'}));
        },
        /** 登出：清除服务端会话与 Cookie（204）。 */
        logout(init = {}) {
            return request('/api/asset-auth/logout', sameOriginInit({...init, method: 'POST'}));
        },
        callback(query = '', init = {}) {
            const suffix = String(query || '').replace(/^\?/, '');
            return request(`/api/asset-auth/callback${suffix ? `?${suffix}` : ''}`, sameOriginInit(init));
        },
        listUsers(init = {}) {
            return request('/api/asset-auth/users', sameOriginInit(init));
        },
        createUser(payload, init = {}) {
            return request('/api/asset-auth/users', jsonInit(payload, sameOriginInit({...init, method: 'POST'})));
        },
        updateUser(userId, payload, init = {}) {
            return request(userUrl(userId), jsonInit(payload, sameOriginInit({...init, method: 'PATCH'})));
        },
        deleteUser(userId, init = {}) {
            return request(userUrl(userId), sameOriginInit({...init, method: 'DELETE'}));
        },
        listTeams(init = {}) {
            return request('/api/asset-auth/teams', sameOriginInit(init));
        },
        createTeam(payload, init = {}) {
            return request('/api/asset-auth/teams', jsonInit(payload, sameOriginInit({...init, method: 'POST'})));
        },
        deleteTeam(teamId, payload, init = {}) {
            return request(
                teamUrl(teamId),
                jsonInit(payload, sameOriginInit({...init, method: 'DELETE'})),
            );
        },
        updateTeamMember(teamId, payload, init = {}) {
            return request(
                `${teamUrl(teamId)}/members`,
                jsonInit(payload, sameOriginInit({...init, method: 'PUT'})),
            );
        },
        deleteTeamMember(teamId, userId, init = {}) {
            return request(
                `${teamUrl(teamId)}/members/${encodeURIComponent(userId)}`,
                sameOriginInit({...init, method: 'DELETE'}),
            );
        },
        listOperationApprovals(query = '', init = {}) {
            const suffix = String(query || '').replace(/^\?/, '');
            return request(
                `/api/asset-auth/operation-approvals${suffix ? `?${suffix}` : ''}`,
                sameOriginInit(init),
            );
        },
        updateOperationApproval(approvalId, payload, init = {}) {
            return request(
                `/api/asset-auth/operation-approvals/${encodeURIComponent(approvalId)}`,
                jsonInit(payload, sameOriginInit({...init, method: 'PUT'})),
            );
        },
        createToken(payload, init = {}) {
            return request('/api/asset-auth/tokens', jsonInit(payload, sameOriginInit({...init, method: 'POST'})));
        },
    };
}

const assetAuthApi = Object.freeze(createAssetAuthApi());
globalThis.GodsWorkbenchAssetAuthApi = assetAuthApi;

export default assetAuthApi;
