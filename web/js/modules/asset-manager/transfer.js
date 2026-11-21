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

import assetManagerHttp from './http.js';

const MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024;
const MAX_TOTAL_BYTES = 32 * 1024 * 1024 * 1024;
const HASH_CHUNK_BYTES = 4 * 1024 * 1024;
const SHA256_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotateRight(value, count) {
    return (value >>> count) | (value << (32 - count));
}

class IncrementalSha256 {
    constructor() {
        this.state = new Uint32Array([
            0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
            0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
        ]);
        this.buffer = new Uint8Array(64);
        this.bufferLength = 0;
        this.bytesHashed = 0;
    }

    update(input) {
        const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
        this.bytesHashed += bytes.length;
        let offset = 0;
        if (this.bufferLength) {
            const needed = 64 - this.bufferLength;
            const take = Math.min(needed, bytes.length);
            this.buffer.set(bytes.subarray(0, take), this.bufferLength);
            this.bufferLength += take;
            offset += take;
            if (this.bufferLength === 64) {
                this._compress(this.buffer);
                this.bufferLength = 0;
            }
        }
        while (offset + 64 <= bytes.length) {
            this._compress(bytes.subarray(offset, offset + 64));
            offset += 64;
        }
        if (offset < bytes.length) {
            this.buffer.set(bytes.subarray(offset), 0);
            this.bufferLength = bytes.length - offset;
        }
    }

    _compress(block) {
        const words = new Uint32Array(64);
        for (let i = 0; i < 16; i += 1) {
            const offset = i * 4;
            words[i] = ((block[offset] << 24) | (block[offset + 1] << 16) | (block[offset + 2] << 8) | block[offset + 3]) >>> 0;
        }
        for (let i = 16; i < 64; i += 1) {
            const x = words[i - 15];
            const y = words[i - 2];
            const sigma0 = rotateRight(x, 7) ^ rotateRight(x, 18) ^ (x >>> 3);
            const sigma1 = rotateRight(y, 17) ^ rotateRight(y, 19) ^ (y >>> 10);
            words[i] = (words[i - 16] + sigma0 + words[i - 7] + sigma1) >>> 0;
        }
        let [a, b, c, d, e, f, g, h] = this.state;
        for (let i = 0; i < 64; i += 1) {
            const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
            const choice = (e & f) ^ (~e & g);
            const t1 = (h + sum1 + choice + SHA256_K[i] + words[i]) >>> 0;
            const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
            const majority = (a & b) ^ (a & c) ^ (b & c);
            const t2 = (sum0 + majority) >>> 0;
            h = g;
            g = f;
            f = e;
            e = (d + t1) >>> 0;
            d = c;
            c = b;
            b = a;
            a = (t1 + t2) >>> 0;
        }
        this.state[0] = (this.state[0] + a) >>> 0;
        this.state[1] = (this.state[1] + b) >>> 0;
        this.state[2] = (this.state[2] + c) >>> 0;
        this.state[3] = (this.state[3] + d) >>> 0;
        this.state[4] = (this.state[4] + e) >>> 0;
        this.state[5] = (this.state[5] + f) >>> 0;
        this.state[6] = (this.state[6] + g) >>> 0;
        this.state[7] = (this.state[7] + h) >>> 0;
    }

    digest() {
        const bitLength = BigInt(this.bytesHashed) * 8n;
        this.buffer[this.bufferLength] = 0x80;
        this.bufferLength += 1;
        if (this.bufferLength > 56) {
            this.buffer.fill(0, this.bufferLength);
            this._compress(this.buffer);
            this.bufferLength = 0;
        }
        this.buffer.fill(0, this.bufferLength, 56);
        for (let i = 0; i < 8; i += 1) {
            this.buffer[63 - i] = Number((bitLength >> BigInt(i * 8)) & 0xffn);
        }
        this._compress(this.buffer);
        return Array.from(this.state, value => value.toString(16).padStart(8, '0')).join('');
    }
}

const transferState = {
    viewGeneration: 0,
    root: null,
    trigger: null,
    context: null,
    transfer: null,
    selectedFiles: [],
    phase: 'idle',
    busy: false,
    uploadRunning: false,
    selectedFilesMatch: false,
    idempotencyKey: '',
    controllers: new Set(),
    message: '',
    messageKind: 'info',
    lastLiveBytes: 0,
};

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
}

function formatBytes(value) {
    const bytes = Math.max(0, Number(value) || 0);
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KiB', 'MiB', 'GiB', 'TiB'];
    let size = bytes;
    let unit = -1;
    do { size /= 1024; unit += 1; } while (size >= 1024 && unit < units.length - 1);
    return `${size.toFixed(size >= 100 ? 0 : size >= 10 ? 1 : 2)} ${units[unit]}`;
}

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function api(path, {method = 'GET', body, headers = {}, signal} = {}) {
    const init = {
        method,
        credentials: 'same-origin',
        headers: {'X-User-Role': 'editor', ...headers},
        signal,
    };
    if (body !== undefined) {
        if (body instanceof File || body instanceof Blob) {
            init.body = body;
        } else {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(body);
        }
    }
    const response = await assetManagerHttp.request(path, init);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(payload?.detail?.message || `请求失败（HTTP ${response.status}）`);
        error.code = payload?.detail?.code || 'TRANSFER_REQUEST_FAILED';
        error.status = response.status;
        throw error;
    }
    return payload;
}

async function hashFile(file, progress) {
    const sha = new IncrementalSha256();
    for (let offset = 0; offset < file.size; offset += HASH_CHUNK_BYTES) {
        const buffer = await file.slice(offset, Math.min(file.size, offset + HASH_CHUNK_BYTES)).arrayBuffer();
        sha.update(new Uint8Array(buffer));
        if (typeof progress === 'function') progress(Math.min(file.size, offset + buffer.byteLength), file.size);
    }
    return sha.digest();
}

function activeTransfer() {
    return transferState.transfer && transferState.transfer.transfer_id;
}

function selectedFileFor(fileRecord, files = transferState.selectedFiles) {
    if (fileRecord.localFile instanceof File) return fileRecord.localFile;
    return files.find(file => file.name === fileRecord.name && file.size === fileRecord.size_bytes) || null;
}

function setMessage(message, kind = 'info') {
    transferState.message = message || '';
    transferState.messageKind = kind;
    const node = transferState.root?.querySelector('[data-transfer-message]');
    if (node) {
        node.textContent = transferState.message;
        node.dataset.kind = kind;
    }
}

function currentPhaseLabel() {
    if (transferState.phase === 'loading') return '正在读取工作区';
    if (transferState.phase === 'hashing') return '正在校验本机文件';
    if (transferState.phase === 'preflight') return '正在复核目标空间与同名素材';
    if (transferState.phase === 'confirming') return '正在确认清单';
    if (transferState.phase === 'uploading') return '正在传输并核验文件';
    if (transferState.phase === 'committing') return '校验完成，正在发布到受管素材区';
    if (transferState.phase === 'retrying') return '正在重新核对后开启新代际';
    if (transferState.phase === 'cancelling') return '正在取消当前代际';
    if (transferState.transfer?.status === 'completed') return '传输已完成';
    if (transferState.transfer?.status === 'ready') return '文件已校验，等待发布';
    if (transferState.transfer?.status === 'failed') return '传输失败，可重试';
    if (transferState.transfer?.status === 'cancelled') return '传输已取消';
    if (transferState.transfer?.status === 'awaiting_confirmation') return '请检查范围与冲突处理';
    if (transferState.transfer?.status === 'uploading') return '等待文件或继续传输';
    return '选择文件后先预检';
}

function renderFileRows() {
    const transfer = transferState.transfer;
    if (transfer?.files?.length) {
        return transfer.files.map(item => {
            const action = item.action || (item.has_name_conflict ? 'skip' : 'upload');
            const options = item.has_name_conflict
                ? `<option value="skip" ${action === 'skip' ? 'selected' : ''}>跳过，保留已有素材</option><option value="rename" ${action === 'rename' ? 'selected' : ''}>重命名后传输</option>`
                : `<option value="upload" ${action === 'upload' ? 'selected' : ''}>传输该文件</option><option value="skip" ${action === 'skip' ? 'selected' : ''}>跳过</option><option value="rename" ${action === 'rename' ? 'selected' : ''}>重命名后传输</option>`;
            const conflict = item.has_name_conflict
                ? `<p class="asset-transfer-conflict">目标已有同名素材${item.conflict_asset_id ? `（${escapeHtml(item.conflict_asset_id)}）` : ''}，请选择跳过或重命名。</p>`
                : '';
            const canChoose = transfer.status === 'awaiting_confirmation';
            const targetName = item.target_name || `${item.name.replace(/(\.[^.]+)?$/, '-copy$1')}`;
            const status = item.status === 'verified' ? '已校验' : item.status === 'committed' ? '已发布' : item.status === 'skipped' ? '已跳过' : item.status === 'failed' ? (item.error || '失败') : item.status === 'receiving' ? '接收中' : '待传输';
            return `<li class="asset-transfer-file-row" data-transfer-file="${escapeHtml(item.file_id)}">
                <div class="asset-transfer-file-main"><div class="asset-transfer-file-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div><div class="asset-transfer-file-meta">${formatBytes(item.size_bytes)} · ${escapeHtml(status)}</div>${conflict}</div>
                ${canChoose ? `<div class="asset-transfer-choice"><label for="transfer-choice-${escapeHtml(item.file_id)}">处理方式</label><select id="transfer-choice-${escapeHtml(item.file_id)}" data-transfer-choice="${escapeHtml(item.file_id)}">${options}</select><label class="asset-transfer-rename" data-transfer-rename-wrap="${escapeHtml(item.file_id)}" ${action === 'rename' ? '' : 'hidden'}>新名称<input type="text" maxlength="255" data-transfer-rename="${escapeHtml(item.file_id)}" value="${escapeHtml(targetName)}" autocomplete="off"></label></div>` : ''}
            </li>`;
        }).join('');
    }
    return transferState.selectedFiles.map((file, index) => `<li class="asset-transfer-file-row"><div class="asset-transfer-file-main"><div class="asset-transfer-file-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div><div class="asset-transfer-file-meta">${formatBytes(file.size)} · ${escapeHtml(transferState.phase === 'hashing' ? '正在计算 SHA-256' : '待预检')}</div></div>${transferState.phase === 'hashing' && index === transferState.hashingIndex ? `<span class="asset-transfer-hash-progress">${formatBytes(transferState.hashingBytes || 0)} / ${formatBytes(file.size)}</span>` : ''}</li>`).join('');
}

function renderDialog() {
    const overlay = transferState.root?.querySelector('#assetTransferOverlay');
    if (!overlay) return;
    const activeElement = overlay.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = activeElement?.id === 'assetTransferFileInput'
        ? 'select'
        : activeElement?.getAttribute('data-transfer-focus');
    const transfer = transferState.transfer;
    const context = transferState.context || {};
    const busy = transferState.busy || transferState.uploadRunning || transferState.phase === 'committing' || transferState.phase === 'cancelling';
    const isPreflight = transfer?.status === 'awaiting_confirmation';
    const isUploading = transfer?.status === 'uploading';
    const isReady = transfer?.status === 'ready';
    const isFailed = ['failed', 'cancelled'].includes(transfer?.status);
    const totalBytes = transfer?.total_bytes || transferState.selectedFiles.reduce((sum, file) => sum + file.size, 0);
    const received = transfer?.received_bytes || 0;
    let actionButton = '';
    if (!transfer || transfer.status === 'awaiting_confirmation') {
        actionButton = `<button class="hw-btn asset-transfer-primary" type="button" data-transfer-${transfer ? 'confirm' : 'preflight'} ${busy || (!transfer && !transferState.selectedFiles.length) ? 'disabled' : ''}>${transfer ? '确认并传输' : '预检并查看清单'}</button>`;
    } else if (isUploading) {
        if (transferState.uploadRunning) {
            actionButton = '<button class="hw-btn asset-transfer-primary" type="button" disabled>正在传输…</button>';
        } else {
            actionButton = `<button class="hw-btn asset-transfer-primary" type="button" data-transfer-resume ${busy || !transferState.selectedFilesMatch ? 'disabled' : ''}>继续传输</button>`;
        }
    } else if (isReady) {
        actionButton = `<button class="hw-btn asset-transfer-primary" type="button" data-transfer-commit ${busy ? 'disabled' : ''}>校验并发布</button>`;
    } else if (isFailed) {
        actionButton = `<button class="hw-btn asset-transfer-primary" type="button" data-transfer-retry ${busy || !transferState.selectedFilesMatch ? 'disabled' : ''}>重新核对并重试</button>`;
    } else if (transfer?.status === 'completed') {
        actionButton = '<button class="hw-btn asset-transfer-primary" type="button" data-transfer-close>完成</button>';
    }
    const canCancel = transfer && !['completed', 'cancelled', 'committing'].includes(transfer.status);
    const space = transfer
        ? `估算 ${formatBytes(transfer.estimated_bytes)} · 可用空间 ${formatBytes(transfer.target_available_bytes)}`
        : '空间与目录权限将在预检时由接收端核验';
    const source = transfer?.source_label || context.source_label || '当前设备（本地选择）';
    const target = transfer?.target_label || context.target_label || context.workspace_label || '读取中';
    const directory = transfer?.directory_id || context.directory_label || '受管素材区';
    const statusText = transfer?.error || transferState.message || currentPhaseLabel();
    overlay.setAttribute('aria-busy', String(busy));
    overlay.innerHTML = `<section id="assetTransferDialog" class="asset-transfer-dialog" role="dialog" aria-modal="true" aria-labelledby="assetTransferTitle" aria-describedby="assetTransferDescription" tabindex="-1">
        <header class="asset-transfer-header"><div><p class="asset-transfer-eyebrow">WORKSPACE TRANSFER</p><h2 id="assetTransferTitle">传输到工作区</h2><p id="assetTransferDescription">逐项确认后上传到受管素材区；不会同步其他文件。</p></div><button class="asset-transfer-close" type="button" data-transfer-close data-transfer-focus="close" aria-label="关闭传输窗口">×</button></header>
        <div class="asset-transfer-summary"><div><span>来源</span><strong>${escapeHtml(source)}</strong></div><div><span>目标</span><strong>${escapeHtml(target)} · ${escapeHtml(directory)}</strong></div><div><span>范围与空间</span><strong>${escapeHtml(transfer ? `${transfer.files.length} 个文件 · ${space}` : space)}</strong></div></div>
        <div class="asset-transfer-body">
            <div class="asset-transfer-select-row"><div><strong>传输清单</strong><span>源文件只从本机选择；服务端不读取任意路径。</span></div><button class="hw-btn" type="button" data-transfer-select data-transfer-focus="select" ${busy ? 'disabled' : ''}>选择文件</button><input id="assetTransferFileInput" type="file" multiple hidden></div>
            <ul class="asset-transfer-file-list" aria-label="传输文件清单">${renderFileRows() || '<li class="asset-transfer-empty">尚未选择文件</li>'}</ul>
            <div class="asset-transfer-progress-block"><div class="asset-transfer-stage"><strong data-transfer-stage>${escapeHtml(statusText)}</strong><span data-transfer-progress-label>${transfer ? `${formatBytes(received)} / ${formatBytes(totalBytes)}` : ''}</span></div><progress data-transfer-progress max="${Math.max(1, totalBytes)}" value="${Math.min(received, totalBytes)}" aria-label="传输进度"></progress><div class="asset-transfer-message" data-transfer-message data-kind="${escapeHtml(transferState.messageKind)}" role="status" aria-live="polite">${escapeHtml(transferState.message || (transfer?.status === 'failed' ? transfer.error : '取消只清理未发布暂存；已发布素材和原文件保留。'))}</div></div>
        </div>
        <footer class="asset-transfer-footer"><button class="hw-btn asset-transfer-cancel" type="button" data-transfer-cancel data-transfer-focus="cancel" ${canCancel && !busy ? '' : 'hidden'}>取消本次传输</button><span class="asset-transfer-footer-spacer"></span>${actionButton.replace('type="button"', 'type="button" data-transfer-focus="primary"')}</footer>
    </section>`;
    const focusTarget = focusKey
        ? overlay.querySelector(`[data-transfer-focus="${CSS.escape(focusKey)}"]`)
        : null;
    if (activeElement) (focusTarget && !focusTarget.disabled && !focusTarget.hidden ? focusTarget : overlay.querySelector('[data-transfer-focus="close"]'))?.focus();
    overlay.setAttribute('aria-busy', String(busy));
    const progress = overlay.querySelector('[data-transfer-progress]');
    if (progress && transfer?.status === 'uploading' && transferState.uploadRunning && received === 0) progress.removeAttribute('value');
}

function syncProgress() {
    const overlay = transferState.root?.querySelector('#assetTransferOverlay');
    if (!overlay) return;
    const transfer = transferState.transfer;
    const total = transfer?.total_bytes || 0;
    const received = transfer?.received_bytes || 0;
    const progress = overlay.querySelector('[data-transfer-progress]');
    if (progress) {
        progress.max = Math.max(1, total);
        if (transferState.uploadRunning && total > 0) progress.value = Math.min(received, total);
        else if (transfer?.status === 'completed') progress.value = progress.max;
    }
    const label = overlay.querySelector('[data-transfer-progress-label]');
    if (label && total) label.textContent = `${formatBytes(received)} / ${formatBytes(total)}`;
    const stage = overlay.querySelector('[data-transfer-stage]');
    if (stage) stage.textContent = transfer?.error || transferState.message || currentPhaseLabel();
    const message = overlay.querySelector('[data-transfer-message]');
    if (message && transferState.message) {
        message.textContent = transferState.message;
        message.dataset.kind = transferState.messageKind;
    }
    if (transfer && received - transferState.lastLiveBytes >= 8 * 1024 * 1024) {
        transferState.lastLiveBytes = received;
        if (message) message.textContent = `已接收 ${formatBytes(received)}，正在继续校验。`;
    }
}

function applyRecord(record) {
    if (!record || !record.transfer_id) return false;
    const current = transferState.transfer;
    if (current?.transfer_id === record.transfer_id) {
        if (Number(record.generation) < Number(current.generation)) return false;
        if (Number(record.generation) === Number(current.generation) && Number(record.version) < Number(current.version)) return false;
    }
    const localFiles = new Map((current?.files || []).map(item => [item.file_id, item.localFile]));
    const selectedByHash = transferState.selectedFiles;
    transferState.transfer = {
        ...record,
        files: (record.files || []).map((item, index) => ({
            ...item,
            localFile: localFiles.get(item.file_id) || selectedByHash[index] || null,
        })),
    };
    transferState.lastLiveBytes = 0;
    syncProgress();
    return true;
}

function apiErrorMessage(error) {
    return error?.message || '传输请求失败，请稍后重试';
}

function setBusy(value, phase = transferState.phase) {
    transferState.busy = Boolean(value);
    transferState.phase = phase;
    renderDialog();
}

function showError(error) {
    setMessage(apiErrorMessage(error), 'error');
    renderDialog();
}

function makeIdempotencyKey() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `transfer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function readSelectedFiles() {
    const input = transferState.root?.querySelector('#assetTransferFileInput');
    const files = Array.from(input?.files || []);
    if (!files.length) return;
    const total = files.reduce((sum, file) => sum + file.size, 0);
    if (files.some(file => file.size <= 0 || file.size > MAX_FILE_BYTES) || total > MAX_TOTAL_BYTES) {
        setMessage('单文件需大于 0 且不超过 8 GiB，单次总量不超过 32 GiB。', 'error');
        renderDialog();
        return;
    }
    transferState.selectedFiles = files;
    transferState.selectedFilesMatch = false;
    if (transferState.transfer && ['failed', 'cancelled', 'uploading'].includes(transferState.transfer.status)) {
        setBusy(true, 'hashing');
        const hashes = [];
        try {
            for (let index = 0; index < files.length; index += 1) {
                transferState.hashingIndex = index;
                transferState.hashingBytes = 0;
                renderDialog();
                const sha256 = await hashFile(files[index], (done) => {
                    transferState.hashingBytes = done;
                    syncProgress();
                });
                hashes.push({file: files[index], sha256});
            }
            const pending = (transferState.transfer.files || []).filter(item => !['skipped', 'committed'].includes(item.status));
            const unused = [...hashes];
            let matched = true;
            for (const record of pending) {
                const index = unused.findIndex(entry => entry.file.name === record.name && entry.file.size === record.size_bytes && entry.sha256 === record.sha256);
                if (index < 0) { matched = false; break; }
                record.localFile = unused[index].file;
                unused.splice(index, 1);
            }
            if (unused.length) matched = false;
            transferState.selectedFilesMatch = matched;
            setMessage(matched ? '所选文件与原始预检清单一致，可以安全重试。' : '文件名、大小或 SHA-256 已变化，请重新预检后再传输。', matched ? 'success' : 'error');
        } catch (error) {
            setMessage(apiErrorMessage(error), 'error');
        } finally {
            transferState.busy = false;
            transferState.phase = 'idle';
            renderDialog();
        }
        return;
    }
    transferState.transfer = null;
    transferState.idempotencyKey = '';
    setMessage(`${files.length} 个文件已选中。预检前不会向工作区写入文件。`, 'info');
    renderDialog();
}

async function calculateFileHashes() {
    const selected = transferState.selectedFiles;
    const hashes = [];
    let accumulated = 0;
    for (let index = 0; index < selected.length; index += 1) {
        const file = selected[index];
        transferState.hashingIndex = index;
        transferState.hashingBytes = 0;
        renderDialog();
        const sha256 = await hashFile(file, done => {
            transferState.hashingBytes = done;
            const progress = transferState.root?.querySelector('[data-transfer-progress]');
            if (progress) {
                progress.max = Math.max(1, file.size);
                progress.value = done;
            }
            const label = transferState.root?.querySelector('[data-transfer-progress-label]');
            if (label) label.textContent = `${formatBytes(done)} / ${formatBytes(file.size)} · 文件 ${index + 1}/${selected.length}`;
            const stage = transferState.root?.querySelector('[data-transfer-stage]');
            if (stage) stage.textContent = `正在计算 ${file.name} 的 SHA-256`;
        });
        hashes.push({name: file.name, size_bytes: file.size, sha256});
        accumulated += file.size;
        transferState.hashingTotalBytes = accumulated;
    }
    return hashes;
}

async function preflight() {
    if (!transferState.context || !transferState.selectedFiles.length) return;
    setBusy(true, 'hashing');
    setMessage('正在读取所选文件并计算完整 SHA-256。', 'info');
    try {
        const files = await calculateFileHashes();
        setBusy(true, 'preflight');
        const record = await api('/api/transfer/preflight', {
            method: 'POST',
            body: {
                target_workspace_id: transferState.context.workspace_id,
                directory_id: transferState.context.directory_id,
                files,
            },
        });
        record.files = record.files.map((item, index) => ({...item, localFile: transferState.selectedFiles[index] || null}));
        transferState.selectedFilesMatch = true;
        transferState.idempotencyKey = makeIdempotencyKey();
        transferState.phase = 'idle';
        transferState.busy = false;
        applyRecord(record);
        setMessage('预检完成。请核对目标、空间和每项同名处理方式，再确认上传。', 'success');
        renderDialog();
    } catch (error) {
        transferState.busy = false;
        transferState.phase = 'idle';
        showError(error);
    }
}

function readChoices() {
    const files = transferState.transfer?.files || [];
    return files.map(item => {
        const select = transferState.root?.querySelector(`[data-transfer-choice="${CSS.escape(item.file_id)}"]`);
        const action = select?.value || (item.has_name_conflict ? 'skip' : 'upload');
        const rename = transferState.root?.querySelector(`[data-transfer-rename="${CSS.escape(item.file_id)}"]`);
        return {file_id: item.file_id, action, new_name: action === 'rename' ? String(rename?.value || '').trim() : null};
    });
}

async function confirmAndUpload() {
    const transfer = transferState.transfer;
    if (!transfer) return;
    const choices = readChoices();
    if (!transferState.idempotencyKey) transferState.idempotencyKey = makeIdempotencyKey();
    setBusy(true, 'confirming');
    try {
        const record = await api('/api/transfer/jobs', {
            method: 'POST',
            headers: {'Idempotency-Key': transferState.idempotencyKey},
            body: {
                preflight_id: transfer.transfer_id,
                expected_version: transfer.version,
                choices,
            },
        });
        applyRecord(record);
        transferState.busy = false;
        transferState.phase = 'idle';
        setMessage('已确认清单，开始逐项上传。', 'info');
        renderDialog();
        if (record.status === 'uploading') await uploadPendingFiles();
    } catch (error) {
        transferState.busy = false;
        transferState.phase = 'idle';
        showError(error);
    }
}

async function pollStatus(transferId, stop) {
    while (!stop.done && activeTransfer() === transferId) {
        await delay(650);
        if (stop.done || activeTransfer() !== transferId) return;
        try {
            const record = await api(`/api/transfer/jobs/${encodeURIComponent(transferId)}`);
            applyRecord(record);
            syncProgress();
            if (record.status !== 'uploading') return;
        } catch (_) {
            // 状态查询失败不改变服务端结论；上传响应或下一次查询仍是权威状态。
        }
    }
}

async function uploadPendingFiles() {
    const transfer = transferState.transfer;
    if (!transfer || transfer.status !== 'uploading' || transferState.uploadRunning) return;
    const pending = transfer.files.filter(item => item.status === 'pending' && item.action !== 'skip');
    if (!pending.length) return;
    const missing = pending.find(item => !selectedFileFor(item));
    if (missing) {
        setMessage(`请重新选择“${missing.name}”，并确保文件内容与预检时一致。`, 'error');
        renderDialog();
        return;
    }
    transferState.uploadRunning = true;
    transferState.phase = 'uploading';
    transferState.message = '';
    renderDialog();
    const stop = {done: false};
    const poller = pollStatus(transfer.transfer_id, stop);
    try {
        for (const item of pending) {
            const current = transferState.transfer;
            if (!current || current.status !== 'uploading') break;
            const file = selectedFileFor(item);
            const controller = new AbortController();
            transferState.controllers.add(controller);
            try {
                const record = await api(
                    `/api/transfer/jobs/${encodeURIComponent(current.transfer_id)}/files/${encodeURIComponent(item.file_id)}?generation=${current.generation}&expected_version=${current.version}`,
                    {
                        method: 'PUT',
                        headers: {'X-Content-SHA256': item.sha256},
                        body: file,
                        signal: controller.signal,
                    },
                );
                applyRecord(record);
            } finally {
                transferState.controllers.delete(controller);
            }
        }
        const latest = await api(`/api/transfer/jobs/${encodeURIComponent(transfer.transfer_id)}`);
        applyRecord(latest);
        if (latest.status === 'ready') setMessage('文件已接收并通过 SHA-256 校验，等待发布。', 'success');
        else if (latest.status === 'failed') setMessage(latest.error || '传输失败，可重新核对后重试。', 'error');
    } catch (error) {
        try {
            const latest = await api(`/api/transfer/jobs/${encodeURIComponent(transfer.transfer_id)}`);
            applyRecord(latest);
        } catch (_) { /* 离线时保留最后一次已确认状态。 */ }
        if (error?.name !== 'AbortError') setMessage(apiErrorMessage(error), 'error');
    } finally {
        stop.done = true;
        await poller;
        transferState.uploadRunning = false;
        transferState.phase = 'idle';
        renderDialog();
    }
}

async function commitTransfer() {
    const transfer = transferState.transfer;
    if (!transfer || transfer.status !== 'ready') return;
    setBusy(true, 'committing');
    setMessage('文件已校验，正在发布并登记到目标工作区。', 'info');
    try {
        const record = await api(`/api/transfer/jobs/${encodeURIComponent(transfer.transfer_id)}/commit`, {
            method: 'POST',
            body: {generation: transfer.generation, expected_version: transfer.version},
        });
        applyRecord(record);
        transferState.busy = false;
        transferState.phase = 'idle';
        const assetIds = record.files.filter(item => item.asset_id).map(item => item.asset_id);
        document.dispatchEvent(new CustomEvent('gw-transfer-completed', {detail: {transferId: record.transfer_id, assetIds}}));
        setMessage('传输完成。资产列表将刷新；源文件与已有同名素材均已保留。', 'success');
        renderDialog();
    } catch (error) {
        transferState.busy = false;
        transferState.phase = 'idle';
        try { applyRecord(await api(`/api/transfer/jobs/${encodeURIComponent(transfer.transfer_id)}`)); } catch (_) {}
        showError(error);
    }
}

async function retryTransfer() {
    const transfer = transferState.transfer;
    if (!transfer || !transferState.selectedFilesMatch) return;
    setBusy(true, 'retrying');
    setMessage('正在重新核对权限与空间，并开启新的传输代际。', 'info');
    try {
        const record = await api(`/api/transfer/jobs/${encodeURIComponent(transfer.transfer_id)}/retry`, {
            method: 'POST',
            body: {expected_version: transfer.version},
        });
        applyRecord(record);
        transferState.busy = false;
        transferState.phase = 'idle';
        setMessage('新代际已开启，旧回调不会改变本次结果。', 'info');
        renderDialog();
        if (record.status === 'uploading') await uploadPendingFiles();
    } catch (error) {
        transferState.busy = false;
        transferState.phase = 'idle';
        showError(error);
    }
}

async function cancelTransfer() {
    const transfer = transferState.transfer;
    if (!transfer || transferState.busy) return;
    transferState.viewGeneration += 1;
    setBusy(true, 'cancelling');
    setMessage('正在让服务端封闭当前代际；旧请求结果不会显示为成功。', 'info');
    try {
        const record = await api(`/api/transfer/jobs/${encodeURIComponent(transfer.transfer_id)}/cancel`, {
            method: 'POST',
            body: {generation: transfer.generation, expected_version: transfer.version},
        });
        applyRecord(record);
        for (const controller of transferState.controllers) controller.abort();
        transferState.uploadRunning = false;
        transferState.busy = false;
        transferState.phase = 'idle';
        setMessage(record.cleanup_pending ? '已取消。当前写入会在关闭后清理，尚未发布的文件不会标记成功。' : '已取消，未发布暂存已清理。', 'success');
        renderDialog();
    } catch (error) {
        transferState.busy = false;
        transferState.phase = 'idle';
        try { applyRecord(await api(`/api/transfer/jobs/${encodeURIComponent(transfer.transfer_id)}`)); } catch (_) {}
        showError(error);
    }
}

function showChoicesForRename(fileId, action) {
    const wrap = transferState.root?.querySelector(`[data-transfer-rename-wrap="${CSS.escape(fileId)}"]`);
    if (wrap) wrap.hidden = action !== 'rename';
}

function openDialog(trigger) {
    if (!transferState.root) return;
    transferState.viewGeneration += 1;
    transferState.trigger = trigger || transferState.trigger;
    transferState.trigger?.setAttribute('aria-expanded', 'true');
    let overlay = transferState.root.querySelector('#assetTransferOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'assetTransferOverlay';
        overlay.className = 'asset-transfer-overlay';
        overlay.hidden = false;
        transferState.root.append(overlay);
        globalThis.FloatingDismissal?.register(overlay, {
            surface: 'asset-transfer',
            kind: 'modal',
            content: '#assetTransferDialog',
            trigger: transferState.trigger,
            close: closeDialog,
        });
    }
    transferState.phase = 'loading';
    transferState.busy = true;
    renderDialog();
    requestAnimationFrame(() => overlay.querySelector('[data-transfer-focus="close"]')?.focus());
    const generation = transferState.viewGeneration;
    void (async () => {
        try {
            transferState.context = await api('/api/transfer/context');
            if (generation !== transferState.viewGeneration) return;
            if (activeTransfer()) {
                const record = await api(`/api/transfer/jobs/${encodeURIComponent(activeTransfer())}`);
                if (generation !== transferState.viewGeneration) return;
                applyRecord(record);
            }
            transferState.busy = false;
            transferState.phase = 'idle';
            renderDialog();
            requestAnimationFrame(() => overlay.querySelector('[data-transfer-select], [data-transfer-close]')?.focus());
        } catch (error) {
            transferState.busy = false;
            transferState.phase = 'idle';
            setMessage(apiErrorMessage(error), 'error');
            renderDialog();
        }
    })();
}

function closeDialog() {
    const overlay = transferState.root?.querySelector('#assetTransferOverlay');
    if (!overlay) return;
    transferState.viewGeneration += 1;
    overlay.remove();
    transferState.trigger?.setAttribute('aria-expanded', 'false');
    transferState.trigger?.focus?.();
}

function init() {
    const root = document.getElementById('assetManagerRoot');
    if (!root || root.dataset.transferReady === 'true') return;
    transferState.root = root;
    root.dataset.transferReady = 'true';
    if (!document.getElementById('assetTransferStyles')) {
        const link = document.createElement('link');
        link.id = 'assetTransferStyles';
        link.rel = 'stylesheet';
        link.href = '/static/css/pages/asset-transfer.css';
        document.head.append(link);
    }
    root.addEventListener('click', event => {
        const target = event.target && typeof event.target.closest === 'function' ? event.target : null;
        if (!target) return;
        const openButton = target.closest('[data-transfer-open]');
        if (openButton) {
            event.preventDefault();
            event.stopPropagation();
            openDialog(openButton);
            return;
        }
        if (target.closest('#assetTransferOverlay') === null) return;
        if (target.closest('[data-transfer-close]')) { event.preventDefault(); closeDialog(); return; }
        if (target.closest('[data-transfer-select]')) { event.preventDefault(); root.querySelector('#assetTransferFileInput')?.click(); return; }
        if (target.closest('[data-transfer-preflight]')) { event.preventDefault(); void preflight(); return; }
        if (target.closest('[data-transfer-confirm]')) { event.preventDefault(); void confirmAndUpload(); return; }
        if (target.closest('[data-transfer-resume]')) { event.preventDefault(); void uploadPendingFiles(); return; }
        if (target.closest('[data-transfer-commit]')) { event.preventDefault(); void commitTransfer(); return; }
        if (target.closest('[data-transfer-retry]')) { event.preventDefault(); void retryTransfer(); return; }
        if (target.closest('[data-transfer-cancel]')) { event.preventDefault(); void cancelTransfer(); return; }
        const overlay = root.querySelector('#assetTransferOverlay');
        if (overlay && event.target === overlay) closeDialog();
    });
    root.addEventListener('change', event => {
        const target = event.target && typeof event.target.closest === 'function' ? event.target : null;
        if (!target) return;
        if (target.matches('#assetTransferFileInput')) { void readSelectedFiles(); return; }
        const choice = target.closest('[data-transfer-choice]');
        if (choice) showChoicesForRename(choice.dataset.transferChoice || '', choice.value);
    });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
else init();
