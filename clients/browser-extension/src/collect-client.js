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
 * MVP1-T8b 收集接口客户端（对齐 #338 已合入契约）。
 *
 * - `POST /api/asset-collect/pair`            → `{pair_token}`
 * - `POST /api/asset-collect/url`             → `202 {job_id, status}`
 * - `GET  /api/asset-collect/jobs/{job_id}`   → `{job_id, status, progress, error_code?}`（`X-Collect-Token`）
 *
 * 端点只允许本机回环 2077：扩展不持有任何模型密钥，也不向外部主机发请求。
 * 挂载到 `globalThis.GwCollectClient`，Node 测试直接读取该全局对象。
 */
(function (root) {
    'use strict';

    /** 契约错误码 → 界面文案（DRM / blob / MSE 必须明确提示，不静默失败）。 */
    var ERROR_COPY = {
        no_target: '未找到可收集的素材地址',
        unsupported_url: '该地址暂不支持收集，请换用素材原始链接',
        blob_url: '浏览器内部地址（blob/MSE）无法收集，请换用原始链接',
        data_url: '内嵌 data 地址无法收集，请换用原始链接',
        media_stream: '流媒体地址无法收集，请换用原始链接',
        drm_protected: '该内容受 DRM 版权保护，无法收集',
        ytdlp_required: '该地址需要 yt-dlp，请在工作台安装后重试',
        ytdlp_unavailable: '该地址需要 yt-dlp，请在工作台安装后重试',
        ytdlp_failed: '素材下载失败，请稍后重试',
        unauthorized: '配对令牌已失效，请在工作台重新配对',
        LOCAL_ONLY: '收集接口仅允许本机访问',
        ORIGIN_FORBIDDEN: '收集接口不允许跨站请求',
        app_unreachable: '未找到正在运行的神工坊工作台，请先启动工作台',
        endpoint_not_loopback: '扩展只允许访问本机 127.0.0.1 或 localhost',
        endpoint_not_supported: '扩展只支持本机 2077 端口的工作台',
        pair_failed: '配对失败，请确认工作台已启动并已登录',
        network_failed: '无法连接本机工作台',
        collect_timeout: '收集已提交，请稍后在工作台确认',
        collect_failed: '收集失败，请稍后重试'
    };

    var LOOPBACK_HOSTS = ['127.0.0.1', 'localhost', '[::1]'];
    var SUPPORTED_PORT = '2077';

    function copyFor(code) {
        return ERROR_COPY[code] || ERROR_COPY.collect_failed;
    }

    function taggedError(code, message) {
        var error = new Error(message || copyFor(code));
        error.errorCode = code;
        return error;
    }

    /** 端点必须为本机回环 2077，其余一律拒绝（AC11：端点仅 localhost）。 */
    function normalizeEndpoint(value) {
        var parsed;
        try {
            parsed = new URL(String(value || '').trim());
        } catch (error) {
            throw taggedError('endpoint_not_loopback');
        }
        if (parsed.protocol !== 'http:') throw taggedError('endpoint_not_loopback');
        if (LOOPBACK_HOSTS.indexOf(parsed.hostname.toLowerCase()) === -1) {
            throw taggedError('endpoint_not_loopback');
        }
        if ((parsed.port || '80') !== SUPPORTED_PORT) {
            throw taggedError('endpoint_not_supported');
        }
        return parsed.origin;
    }

    function create(options) {
        var config = options || {};
        var endpoint = normalizeEndpoint(config.endpoint || 'http://127.0.0.1:2077');
        var fetchImpl = config.fetchImpl || function (url, init) {
            return fetch(url, init);
        };
        var now = typeof config.now === 'function' ? config.now : function () { return Date.now(); };
        var wait = typeof config.wait === 'function' ? config.wait : function (ms) {
            return new Promise(function (resolve) { setTimeout(resolve, ms); });
        };

        function call(path, init) {
            var requestInit = Object.assign({credentials: 'same-origin'}, init || {});
            return Promise.resolve().then(function () {
                return fetchImpl(endpoint + path, requestInit);
            }).catch(function (error) {
                throw taggedError('network_failed', (error && error.message) || copyFor('network_failed'));
            });
        }

        function readJson(response) {
            return Promise.resolve().then(function () {
                return response.json();
            }).catch(function () {
                return null;
            }).then(function (body) {
                if (response.ok) return body || {};
                var detail = body && body.detail;
                var code = (detail && detail.code) || (body && body.error_code) || ('http_' + response.status);
                var message = (detail && detail.message) || (body && body.message) || copyFor(code);
                throw taggedError(code, message);
            });
        }

        function pair() {
            return call('/api/asset-collect/pair', {method: 'POST'}).then(readJson).then(function (body) {
                var token = String((body && body.pair_token) || '');
                if (!token) throw taggedError('pair_failed');
                return token;
            });
        }

        function submitUrl(url, token) {
            return call('/api/asset-collect/url', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url, pair_token: token || ''})
            }).then(readJson).then(function (body) {
                return {
                    jobId: String((body && body.job_id) || ''),
                    status: String((body && body.status) || 'queued')
                };
            });
        }

        function getJob(jobId, token) {
            return call('/api/asset-collect/jobs/' + encodeURIComponent(jobId), {
                method: 'GET',
                headers: {'X-Collect-Token': token || ''}
            }).then(readJson).then(function (body) {
                return {
                    jobId: String((body && body.job_id) || jobId),
                    status: String((body && body.status) || 'queued'),
                    progress: Number((body && body.progress) || 0),
                    errorCode: String((body && body.error_code) || ''),
                    message: String((body && body.message) || '')
                };
            });
        }

        /**
         * 提交并轮询到终态：默认 2500ms 预算，保证扩展侧 ≤3s 入库（AC11）。
         * 超预算不算失败，返回 `timedOut`，避免误报「收集失败」。
         */
        function collectUrl(url, options2) {
            var settings = options2 || {};
            var token = settings.token || '';
            var deadlineMs = Number.isFinite(settings.deadlineMs) ? settings.deadlineMs : 2500;
            var intervalMs = Number.isFinite(settings.intervalMs) ? settings.intervalMs : 150;
            var startedAt = now();
            return submitUrl(url, token).then(function (created) {
                var status = created.status;
                var progress = 0;
                var errorCode = '';
                var message = '';

                function step() {
                    if (status === 'done' || status === 'failed') {
                        var elapsed = now() - startedAt;
                        return {
                            ok: status === 'done',
                            jobId: created.jobId,
                            status: status,
                            progress: progress,
                            errorCode: errorCode,
                            message: message || (errorCode ? copyFor(errorCode) : ''),
                            elapsedMs: elapsed,
                            timedOut: false
                        };
                    }
                    if (now() - startedAt >= deadlineMs) {
                        return {
                            ok: false,
                            jobId: created.jobId,
                            status: status,
                            progress: progress,
                            errorCode: 'collect_timeout',
                            message: copyFor('collect_timeout'),
                            elapsedMs: now() - startedAt,
                            timedOut: true
                        };
                    }
                    return wait(intervalMs).then(function () {
                        return getJob(created.jobId, token);
                    }).then(function (job) {
                        status = job.status;
                        progress = job.progress;
                        errorCode = job.errorCode;
                        message = job.message;
                        return step();
                    });
                }

                return step();
            });
        }

        return {
            endpoint: endpoint,
            pair: pair,
            submitUrl: submitUrl,
            getJob: getJob,
            collectUrl: collectUrl
        };
    }

    root.GwCollectClient = {
        ERROR_COPY: ERROR_COPY,
        SUPPORTED_PORT: SUPPORTED_PORT,
        copyFor: copyFor,
        normalizeEndpoint: normalizeEndpoint,
        create: create
    };
})(typeof globalThis !== 'undefined' ? globalThis : this);
