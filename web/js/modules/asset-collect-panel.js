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
 * MVP1-T8a-UI 素材收集面板（mock API，issue #346）。
 *
 * mock 契约由本单定义，#338 后端落地后仅替换 adapter，不再改 UI：
 * - POST /api/asset-collect/url    {url, pair_token} → 202 {job_id, status}
 * - POST /api/asset-collect/files  multipart files[] + pair_token → 202 {job_id, accepted:[{name,size}]}
 * - GET  /api/asset-collect/jobs/{job_id} → {job_id, status, progress, error_code?}
 *
 * 本模块顶层不触碰 DOM，node 测试可直接 import 纯逻辑层。
 */

import { formatFileSize } from './asset-manager/formatters.js';

/** 失败文案：契约 error_code → 面板展示文案（AC3：DRM/blob 明确失败且不重试）。 */
export const COLLECT_ERROR_COPY = {
    unsupported_url: '链接格式暂不支持，请更换素材来源',
    drm_protected: '该内容受 DRM 版权保护，无法收集',
    blob_url: '浏览器内部地址无法收集，请改用原始链接',
    unauthorized: '配对令牌无效或已过期，请重新配对',
};

/** mock/adapter 的任务状态集合与终态判定。 */
const JOB_STATUS_COPY = { queued: '排队中', running: '收集中', done: '完成', failed: '失败' };

export function isTerminalJobStatus(status) {
    return status === 'done' || status === 'failed';
}

/**
 * AC4：空 URL 与非 http(s) 一律前端拦截，不发请求。
 * blob:/data:/javascript: 等内部协议同样拦截。
 */
export function isValidCollectUrl(value) {
    if (typeof value !== 'string') return false;
    const trimmed = value.trim();
    if (!trimmed) return false;
    let parsed;
    try {
        parsed = new URL(trimmed);
    } catch {
        return false;
    }
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
}

/** mock 内置失败启发式：URL 含 drm/blob 关键字时按契约错误码失败，便于 AC3 演示。 */
function mockCollectFailureFor(url) {
    const lower = String(url || '').toLowerCase();
    if (lower.includes('drm')) return 'drm_protected';
    if (lower.includes('blob')) return 'blob_url';
    return '';
}

/**
 * mock adapter（默认实现，无网络依赖）。
 * options.tickMs 自动推进间隔；options.autoTick=false 时用 advance() 手动推进（测试）；
 * options.failFor(url) 可注入提交失败，缺省用内置启发式。
 */
export function createMockCollectAdapter(options = {}) {
    const tickMs = Number.isFinite(options.tickMs) ? options.tickMs : 300;
    const step = Number.isFinite(options.step) ? options.step : 25;
    const failFor = typeof options.failFor === 'function' ? options.failFor : mockCollectFailureFor;
    const jobs = new Map();
    let seq = 0;
    let timer = null;

    /** 自动推进：每 tick 走一步，无活动任务后自动停表；autoTick=false 仅供测试手动 advance()。 */
    function scheduleTick() {
        if (timer || options.autoTick === false) return;
        timer = setTimeout(() => {
            timer = null;
            if (advance()) scheduleTick();
        }, tickMs);
    }

    /** 推进一步：queued→running→done；返回是否仍存在活动任务。 */
    function advance() {
        let active = false;
        for (const job of jobs.values()) {
            if (job.status === 'queued') {
                job.status = 'running';
                job.progress = Math.min(step, 99);
                active = true;
            } else if (job.status === 'running') {
                job.progress = Math.min(job.progress + step, 100);
                if (job.progress >= 100) job.status = 'done';
                else active = true;
            }
        }
        return active;
    }

    function rejectIfFailed(url) {
        const errorCode = failFor(url);
        if (!errorCode) return null;
        const error = new Error(COLLECT_ERROR_COPY[errorCode] || '收集失败');
        error.errorCode = errorCode;
        return error;
    }

    return {
        /** 契约：POST /api/asset-collect/url → 202 {job_id, status}；失败按契约 reject {errorCode}。 */
        async submitUrl(url, pairToken) {
            void pairToken;
            const failure = rejectIfFailed(url);
            if (failure) throw failure;
            seq += 1;
            const job = { jobId: `mock-collect-${seq}`, status: 'queued', progress: 0 };
            jobs.set(job.jobId, job);
            scheduleTick();
            return { jobId: job.jobId, status: job.status };
        },
        /** 契约：POST /api/asset-collect/files → 202 {job_id, accepted:[{name,size}]}。 */
        async submitFiles(files, pairToken) {
            void pairToken;
            const accepted = [...files].map(file => ({ name: String(file.name || ''), size: Number(file.size || 0) }));
            seq += 1;
            const job = { jobId: `mock-collect-${seq}`, status: 'queued', progress: 0 };
            jobs.set(job.jobId, job);
            scheduleTick();
            return { jobId: job.jobId, accepted };
        },
        /** 契约：GET /api/asset-collect/jobs/{job_id} → {job_id, status, progress, error_code?}。 */
        async getJob(jobId) {
            const job = jobs.get(String(jobId || ''));
            if (!job) {
                const error = new Error('收集任务不存在');
                error.errorCode = 'unsupported_url';
                throw error;
            }
            return { jobId: job.jobId, status: job.status, progress: job.progress, errorCode: '' };
        },
        advance,
        dispose() {
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
            jobs.clear();
        },
    };
}

/**
 * HTTP adapter（#338 后端落地后由面板 options.adapter 切换，本期不接线）。
 * 路径字面量保持内联完整模板串，供 tests/contracts/projects/test_phase8_frontend_backend_api_gap.py 扫描。
 */
export function createHttpCollectAdapter(fetchImpl = (...args) => fetch(...args)) {
    async function readJson(res) {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            const error = new Error(String(data?.message || `收集请求失败（HTTP ${res.status}）`));
            error.errorCode = String(data?.error_code || '');
            throw error;
        }
        return data;
    }

    return {
        async submitUrl(url, pairToken) {
            const data = await readJson(await fetchImpl('/api/asset-collect/url', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, pair_token: pairToken }),
            }));
            return { jobId: String(data.job_id || ''), status: String(data.status || 'queued') };
        },
        async submitFiles(files, pairToken) {
            const form = new FormData();
            for (const file of files) form.append('files[]', file);
            form.append('pair_token', pairToken);
            const data = await readJson(await fetchImpl('/api/asset-collect/files', { method: 'POST', body: form }));
            return {
                jobId: String(data.job_id || ''),
                accepted: (Array.isArray(data.accepted) ? data.accepted : []).map(item => ({
                    name: String(item?.name || ''),
                    size: Number(item?.size || 0),
                })),
            };
        },
        async getJob(jobId) {
            const data = await readJson(await fetchImpl(`/api/asset-collect/jobs/${encodeURIComponent(jobId)}`));
            return {
                jobId: String(data.job_id || jobId),
                status: String(data.status || 'queued'),
                progress: Number(data.progress || 0),
                errorCode: String(data.error_code || ''),
            };
        },
    };
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** 单个收集任务行 HTML（纯函数，node 测试直接断言）。 */
export function renderCollectJobItemHtml(job) {
    const status = JOB_STATUS_COPY[job.status] ? job.status : 'queued';
    const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
    const files = Array.isArray(job.files) ? job.files : [];
    const filesHtml = files.length
        ? `<div class="asset-collect-files">${files.map(file =>
            `<span class="asset-collect-file"><span class="asset-collect-file-name">${escapeHtml(file.name)}</span><small>${escapeHtml(formatFileSize(file.size))}</small></span>`
        ).join('')}</div>`
        : '';
    const errorHtml = status === 'failed'
        ? `<p class="asset-collect-error" data-collect-error>${escapeHtml(COLLECT_ERROR_COPY[job.errorCode] || job.errorCode || '收集失败，请检查素材来源')}</p>`
        : '';
    return `<li class="asset-collect-job is-${status}" data-collect-job-id="${escapeHtml(job.jobId)}">
        <div class="asset-collect-job-head">
            <span class="asset-collect-job-title" title="${escapeHtml(job.title || '')}">${escapeHtml(job.title || '')}</span>
            <span class="asset-collect-status">${JOB_STATUS_COPY[status]}</span>
        </div>
        ${filesHtml}
        <div class="asset-collect-progress" role="progressbar" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${progress}%"></span></div>
        ${errorHtml}
    </li>`;
}

const COLLECT_STYLESHEET_HREF = '/static/css/pages/asset-collect.css';

/** 页面 HTML 不可改（不在本单可改文件清单），样式由面板首次挂载时注入并按 href 幂等。 */
export function ensureAssetCollectStyles(doc) {
    const target = doc || (typeof document !== 'undefined' ? document : null);
    if (!target || !target.head) return;
    if (target.querySelector(`link[data-asset-collect-styles="${COLLECT_STYLESHEET_HREF}"]`)) return;
    const link = target.createElement('link');
    link.rel = 'stylesheet';
    link.href = COLLECT_STYLESHEET_HREF;
    link.dataset.assetCollectStyles = COLLECT_STYLESHEET_HREF;
    target.head.appendChild(link);
}

const PANEL_MARKUP = `
    <section class="asset-collect-panel" role="dialog" aria-modal="true" aria-label="素材收集"
             data-floating-surface="asset-collect-panel"
             data-floating-content="self"
             data-floating-dismiss="outside"
             data-floating-escape="close-only">
        <header class="asset-collect-head">
            <div class="asset-collect-heading">
                <strong>素材收集</strong>
                <small>粘贴链接或拖入文件，后台任务自动收集入库</small>
            </div>
            <button type="button" class="asset-collect-close" data-collect-close data-floating-close aria-label="关闭收集面板">&times;</button>
        </header>
        <form class="asset-collect-url-form" data-collect-url-form novalidate>
            <input class="asset-collect-url-input" data-collect-url-input type="url" name="collect-url"
                   placeholder="粘贴素材页面链接（http/https）" aria-label="素材链接" autocomplete="off" />
            <button type="submit" class="asset-collect-submit hw-btn hw-btn-gold">开始收集</button>
        </form>
        <p class="asset-collect-hint" data-collect-hint hidden></p>
        <div class="asset-collect-drop" data-collect-drop tabindex="0" role="button" aria-label="拖放文件或按回车选择文件">
            <i data-lucide="cloud-download" aria-hidden="true"></i>
            <strong>拖放文件到此处</strong>
            <small>或点击选择文件</small>
            <input type="file" multiple class="asset-collect-file-input" data-collect-file-input hidden />
        </div>
        <ul class="asset-collect-jobs" data-collect-jobs></ul>
        <p class="asset-collect-empty" data-collect-empty>暂无收集任务</p>
    </section>
`;

/**
 * 收集面板浮窗（GW-UI-FW-01：显式关闭按钮、Escape 关闭、点外关闭、焦点闭环、关闭还原焦点）。
 * options：{adapter, pairToken, pollIntervalMs, document}；返回控制对象，destroy() 释放定时器与 DOM。
 */
export function createAssetCollectPanel(options = {}) {
    const doc = options.document || (typeof document !== 'undefined' ? document : null);
    if (!doc || !doc.body) throw new Error('素材收集面板需要 document 环境');
    const adapter = options.adapter || createMockCollectAdapter();
    const pairToken = typeof options.pairToken === 'string' && options.pairToken ? options.pairToken : 'local-pair-token';
    const pollIntervalMs = Number.isFinite(options.pollIntervalMs) ? options.pollIntervalMs : 500;

    const jobs = [];
    let pollTimer = null;
    let pollInFlight = false;
    let lastTrigger = null;

    const overlay = doc.createElement('div');
    overlay.className = 'asset-collect-overlay';
    overlay.hidden = true;
    overlay.innerHTML = PANEL_MARKUP;
    doc.body.appendChild(overlay);

    const panel = overlay.querySelector('.asset-collect-panel');
    const urlForm = overlay.querySelector('[data-collect-url-form]');
    const urlInput = overlay.querySelector('[data-collect-url-input]');
    const hint = overlay.querySelector('[data-collect-hint]');
    const dropZone = overlay.querySelector('[data-collect-drop]');
    const fileInput = overlay.querySelector('[data-collect-file-input]');
    const jobsList = overlay.querySelector('[data-collect-jobs]');
    const emptyTip = overlay.querySelector('[data-collect-empty]');

    function isOpen() {
        return !overlay.hidden;
    }

    function renderList() {
        jobsList.innerHTML = jobs.map(renderCollectJobItemHtml).join('');
        emptyTip.hidden = jobs.length > 0;
    }

    function showHint(message) {
        if (!message) {
            hint.hidden = true;
            hint.textContent = '';
            return;
        }
        hint.hidden = false;
        hint.textContent = message;
    }

    function appendJob(entry) {
        jobs.unshift(entry);
        renderList();
        return entry;
    }

    function schedulePoll() {
        if (pollTimer || !isOpen()) return;
        pollTimer = setTimeout(() => {
            pollTimer = null;
            pollNow().catch(() => {});
        }, pollIntervalMs);
    }

    /** 对未终态任务轮询一次（测试可直接调用，不依赖定时器）。 */
    async function pollNow() {
        if (pollInFlight) return;
        pollInFlight = true;
        try {
            const pending = jobs.filter(job => !isTerminalJobStatus(job.status) && job.jobId);
            for (const job of pending) {
                try {
                    const next = await adapter.getJob(job.jobId);
                    job.status = next.status;
                    job.progress = next.progress;
                    job.errorCode = next.errorCode || '';
                } catch (err) {
                    job.status = 'failed';
                    job.errorCode = err?.errorCode || '';
                }
            }
            if (pending.length) renderList();
        } finally {
            pollInFlight = false;
        }
        if (jobs.some(job => !isTerminalJobStatus(job.status) && job.jobId)) schedulePoll();
    }

    /** AC4：空/非 http(s) 在此拦截，仅更新提示，不发起提交。 */
    async function submitUrl(rawValue) {
        const url = String(rawValue || '').trim();
        if (!isValidCollectUrl(url)) {
            showHint('请输入以 http(s) 开头的有效素材链接');
            return null;
        }
        showHint('');
        const entry = appendJob({ jobId: '', kind: 'url', title: url, files: [], status: 'queued', progress: 0, errorCode: '' });
        try {
            const created = await adapter.submitUrl(url, pairToken);
            entry.jobId = created.jobId;
            entry.status = created.status || 'queued';
        } catch (err) {
            entry.status = 'failed';
            entry.errorCode = err?.errorCode || '';
            entry.progress = 0;
        }
        renderList();
        if (!isTerminalJobStatus(entry.status)) schedulePoll();
        return entry;
    }

    /** AC2：多文件提交后按 accepted 清单展示每个文件名与大小。 */
    async function submitFiles(fileList) {
        const files = [...(fileList || [])];
        if (!files.length) return null;
        const entry = appendJob({
            jobId: '',
            kind: 'files',
            title: `本地文件 ×${files.length}`,
            files: files.map(file => ({ name: String(file.name || ''), size: Number(file.size || 0) })),
            status: 'queued',
            progress: 0,
            errorCode: '',
        });
        try {
            const created = await adapter.submitFiles(files, pairToken);
            entry.jobId = created.jobId;
            entry.status = 'queued';
            if (Array.isArray(created.accepted) && created.accepted.length) entry.files = created.accepted;
        } catch (err) {
            entry.status = 'failed';
            entry.errorCode = err?.errorCode || '';
            entry.progress = 0;
        }
        renderList();
        if (!isTerminalJobStatus(entry.status)) schedulePoll();
        return entry;
    }

    function focusables() {
        return [...panel.querySelectorAll('button, input, [tabindex]')].filter(el => !el.hidden && el.offsetParent !== null);
    }

    function onKeydown(event) {
        if (!isOpen()) return;
        if (event.key === 'Escape') {
            event.stopPropagation();
            close();
            return;
        }
        if (event.key === 'Tab') {
            const items = focusables();
            if (!items.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (event.shiftKey && (event.target === first || !panel.contains(event.target))) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && !panel.contains(event.target)) {
                event.preventDefault();
                first.focus();
            }
        }
    }

    function open(trigger) {
        ensureAssetCollectStyles(doc);
        lastTrigger = trigger && typeof trigger.focus === 'function' ? trigger : lastTrigger;
        overlay.hidden = false;
        if (window?.lucide?.createIcons) window.lucide.createIcons();
        urlInput.focus();
        schedulePoll();
    }

    function close() {
        overlay.hidden = true;
        showHint('');
        lastTrigger?.focus?.();
    }

    overlay.addEventListener('click', event => {
        if (event.target === overlay) close();
    });
    overlay.querySelector('[data-collect-close]').addEventListener('click', close);
    urlForm.addEventListener('submit', event => {
        event.preventDefault();
        submitUrl(urlInput.value);
    });
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            fileInput.click();
        }
    });
    dropZone.addEventListener('dragover', event => {
        event.preventDefault();
        dropZone.classList.add('is-dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('is-dragover'));
    dropZone.addEventListener('drop', event => {
        event.preventDefault();
        dropZone.classList.remove('is-dragover');
        submitFiles(event.dataTransfer?.files);
    });
    fileInput.addEventListener('change', () => {
        submitFiles(fileInput.files);
        fileInput.value = '';
    });
    doc.addEventListener('keydown', onKeydown);

    return {
        open,
        close,
        isOpen,
        submitUrl,
        submitFiles,
        pollNow,
        renderList,
        jobs,
        destroy() {
            if (pollTimer) {
                clearTimeout(pollTimer);
                pollTimer = null;
            }
            doc.removeEventListener('keydown', onKeydown);
            overlay.remove();
            adapter.dispose?.();
        },
    };
}

let sharedPanel = null;

/** asset-manager 入口：打开共享收集面板（首次懒创建并注入样式）。 */
export function openAssetCollectOverlay(trigger, options = {}) {
    ensureAssetCollectStyles();
    if (!sharedPanel) sharedPanel = createAssetCollectPanel(options);
    sharedPanel.open(trigger);
    return sharedPanel;
}
