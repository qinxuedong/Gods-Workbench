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
 * QIN-619（#748）：前端治理查重全量 SHA 确认与任务中心联动测试。
 *
 * 覆盖：
 * 1. 选项 A 状态边界：suspected 隐藏确认按钮并引导核验；confirmed 展示完整 SHA 徽标与确认按钮；
 * 2. 卡片选中态（.is-selected / aria-selected）与点击切换；
 * 3. 慢响应（1500ms）与并发锁：在途期间按钮禁用且不重复提交，绝不提前将 suspected 标为 confirmed；
 * 4. 重入/关闭抽屉（generation + AbortController）：旧在途请求返回后不得污染当前界面或残留轮询；
 * 5. 写后刷新失败保护（governanceRefreshNotice）：POST 确认成功后若刷新 GET 失败，保留原结果并展示刷新提示与重试；
 * 6. task-center.js + i18n/task-center.js：asset.dedup.confirm 类型映射、paused 状态不被误判为 cancelled、选中行 .is-selected。
 */

import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
    createAssetViewHost,
    installHost,
    wait,
} from './qin-602-asset-view-harness.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

const REAL_SET_INTERVAL = globalThis.setInterval;
const ACTIVE_INTERVALS = new Set();

before(() => {
    globalThis.setInterval = (...args) => {
        const handle = REAL_SET_INTERVAL(...args);
        handle?.unref?.();
        ACTIVE_INTERVALS.add(handle);
        return handle;
    };
});

after(() => {
    for (const handle of ACTIVE_INTERVALS) {
        globalThis.clearInterval(handle);
    }
    ACTIVE_INTERVALS.clear();
    globalThis.setInterval = REAL_SET_INTERVAL;
});

function jsonRes(data, status = 200) {
    return {
        status,
        ok: status >= 200 && status < 300,
        headers: { get: key => (String(key).toLowerCase() === 'content-type' ? 'application/json' : null) },
        json: async () => data,
        text: async () => JSON.stringify(data),
    };
}

function makeSuggestionsPayload({ suspected = true, confirmed = true, revision = 5 } = {}) {
    const duplicate_files = [];
    if (suspected) {
        duplicate_files.push({
            suggestion_id: 'dup_suspected_01',
            dedup_status: 'suspected',
            dedup_reason: 'sampled_sha256',
            content_hash: 'a'.repeat(64),
            size_bytes: 4096,
            asset_ids: ['ast_1', 'ast_2'],
            members: [
                { asset_id: 'ast_1', path: 'roots/demo/clip_1.mp4', size_bytes: 4096, hash_mode: 'sampled' },
                { asset_id: 'ast_2', path: 'roots/demo/clip_2.mp4', size_bytes: 4096, hash_mode: 'sampled' },
            ],
        });
    }
    if (confirmed) {
        duplicate_files.push({
            suggestion_id: 'dup_confirmed_01',
            dedup_status: 'confirmed',
            dedup_reason: 'full_sha256',
            content_hash: 'b'.repeat(64),
            size_bytes: 2048,
            asset_ids: ['ast_3', 'ast_4'],
            members: [
                { asset_id: 'ast_3', path: 'roots/demo/still_1.png', size_bytes: 2048, hash_mode: 'full' },
                { asset_id: 'ast_4', path: 'roots/demo/still_2.png', size_bytes: 2048, hash_mode: 'full' },
            ],
        });
    }
    return {
        revision,
        data_status: 'ok',
        data_gaps: [],
        dedup_summary: {
            suspected_groups: suspected ? 1 : 0,
            confirmed_groups: confirmed ? 1 : 0,
        },
        suggestions: {
            naming: [],
            tagging: [],
            duplicate_files,
            duplicate_shots: [],
            vector_duplicates: [],
            similar_names: [],
        },
    };
}

function makeBaseFetch(routesHandler) {
    return (url, init = {}, entry = {}) => {
        const target = String(url);
        if (target.includes('/api/asset-inbox')) {
            return Promise.resolve(jsonRes({ items: [], settings: { unread_count: 0, is_default: true } }));
        }
        if (target.includes('/api/asset-registry/status')) {
            return Promise.resolve(jsonRes({
                roots: [{ root_id: 'root_demo', label: 'Demo', path: 'roots/demo', available: true }],
                jobs: [],
                data_status: 'ok',
            }));
        }
        if (target.includes('/api/asset-registry/facets')) {
            return Promise.resolve(jsonRes({ kinds: [], tags: [], total: 0 }));
        }
        if (target.includes('/api/asset-registry?')) {
            return Promise.resolve(jsonRes({ items: [], total: 0, limit: 120, offset: 0, data_status: 'ok' }));
        }
        if (target.includes('/api/asset-library/')) {
            return Promise.resolve(jsonRes({ items: [], boards: [], order: ['registry'], hidden: [] }));
        }
        if (routesHandler) {
            const handled = routesHandler(target, init, entry);
            if (handled) return handled;
        }
        return Promise.resolve(jsonRes({ ok: true }));
    };
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

test('QIN-619 AC15/选项A：suspected 隐藏确认按钮并引导核验；confirmed 允许确认；支持卡片选中态', async () => {
    const fetchImpl = makeBaseFetch((target) => {
        if (target.includes('/api/asset-registry/governance/suggestions')) {
            return Promise.resolve(jsonRes(makeSuggestionsPayload({ suspected: true, confirmed: true })));
        }
        return null;
    });
    const host = await createAssetViewHost({ embedded: true, tab: 'registry', fetchImpl });
    try {
        await host.main.gwCall('governance', 'openGovernanceDrawer', null);
        await wait(60);

        const drawer = host.document.querySelector('[data-governance-drawer-dialog]');
        assert.ok(drawer, '治理建议抽屉必须打开');

        const bar = drawer.querySelector('[data-dedup-bar]');
        assert.ok(bar, 'duplicate_files 分组顶部必须渲染完整 SHA 核验汇总条');
        const startBtn = drawer.querySelector('[data-dedup-start]');
        assert.ok(startBtn && !startBtn.hasAttribute('disabled'), '存在疑似重复组时核验按钮必须可点击');

        const suspectedCard = drawer.querySelector('[data-governance-suggestion="dup_suspected_01"]');
        assert.ok(suspectedCard, '必须渲染疑似重复卡片');
        assert.ok(
            suspectedCard.querySelector('.governance-dedup-badge.is-suspected[data-dedup-status="suspected"]'),
            '疑似重复卡片必须展示 suspected 状态徽标'
        );
        assert.equal(
            suspectedCard.querySelector('[data-governance-confirm="dup_suspected_01"]'),
            null,
            '选项 A：suspected 卡片绝不允许出现确认建议按钮'
        );
        assert.ok(
            suspectedCard.querySelector('[data-dedup-suspected-hint]'),
            'suspected 卡片必须展示引导完成完整 SHA 核验的只读提示'
        );

        const confirmedCard = drawer.querySelector('[data-governance-suggestion="dup_confirmed_01"]');
        assert.ok(confirmedCard, '必须渲染已确认重复卡片');
        assert.ok(
            confirmedCard.querySelector('.governance-dedup-badge.is-confirmed[data-dedup-status="confirmed"]'),
            '已确认卡片必须展示 confirmed 状态徽标'
        );
        assert.ok(
            confirmedCard.querySelector('[data-governance-confirm="dup_confirmed_01"]'),
            'confirmed 卡片必须展示确认建议按钮'
        );

        // 验证选中态切换
        await host.main.gwCall('governance', 'selectGovernanceSuggestion', 'dup_suspected_01');
        const selected = drawer.querySelector('[data-governance-suggestion="dup_suspected_01"]');
        assert.ok(selected.classList.contains('is-selected'), '选中卡片必须带 .is-selected 类');
        assert.equal(selected.getAttribute('aria-selected'), 'true');

        await host.main.gwCall('governance', 'selectGovernanceSuggestion', 'dup_suspected_01');
        const deselected = drawer.querySelector('[data-governance-suggestion="dup_suspected_01"]');
        assert.ok(!deselected.classList.contains('is-selected'), '再次选择同一卡片应取消选中态');
        assert.equal(deselected.getAttribute('aria-selected'), 'false');
    } finally {
        await host.main.gwCall('governance', 'renderGovernanceDrawer');
        host.document.querySelector('[data-governance-drawer-close]')?.click?.();
        host.dispose();
    }
});

test('QIN-619 慢请求与并发锁：提交在途期间锁定按钮、防重复提交且绝不提前标为 confirmed', async () => {
    const postGate = deferred();
    let postCalls = 0;
    let pollCalls = 0;
    let currentPayload = makeSuggestionsPayload({ suspected: true, confirmed: false, revision: 10 });

    const fetchImpl = makeBaseFetch((target, init) => {
        if (target.endsWith('/api/asset-registry/governance/dedup-confirm-jobs') && init?.method === 'POST') {
            postCalls += 1;
            return postGate.promise;
        }
        if (target.includes('/api/asset-registry/workspace-jobs/job_dedup_slow')) {
            pollCalls += 1;
            return Promise.resolve(jsonRes({
                job: {
                    job_id: 'job_dedup_slow',
                    job_type: 'asset.dedup.confirm',
                    status: 'succeeded',
                    progress: { processed: 2, total: 2, percent: 100 },
                },
            }));
        }
        if (target.includes('/api/asset-registry/governance/suggestions')) {
            return Promise.resolve(jsonRes(currentPayload));
        }
        return null;
    });

    const host = await createAssetViewHost({ embedded: true, tab: 'registry', fetchImpl });
    try {
        await host.main.gwCall('governance', 'openGovernanceDrawer', null);
        await wait(60);
        const { GW_DEFERRED } = await import(
            pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/state.js')).href
        );

        // 发起慢请求（真实 1500ms 延迟），直接触发域函数面以真实模拟按钮连击并发场景
        const t0 = Date.now();
        setTimeout(() => {
            currentPayload = makeSuggestionsPayload({ suspected: false, confirmed: true, revision: 11 });
            postGate.resolve(jsonRes({
                job_id: 'job_dedup_slow',
                job: { job_id: 'job_dedup_slow', status: 'queued', progress: { processed: 0, total: 2, percent: 0 } },
                poll_hint: { url: '/api/asset-registry/workspace-jobs/job_dedup_slow', interval_ms: 10 },
            }, 202));
        }, 1500);
        const firstRun = GW_DEFERRED.governance.startDedupConfirm({ pollIntervalMs: 10 });
        await wait(80);

        // 并发第二次点击必须被 dedupConfirmLoading 锁拦截，不能发第二次 POST
        await GW_DEFERRED.governance.startDedupConfirm({ pollIntervalMs: 10 });
        assert.equal(postCalls, 1, '提交在途并发点击不得发出重复 POST 请求');
        assert.equal(host.main.dedupConfirmLoading, true, '慢响应在途期间必须保持 loading 状态');

        const startBtn = host.document.querySelector('[data-dedup-start]');
        assert.ok(startBtn?.hasAttribute('disabled'), '慢响应在途期间必须禁用核验触发按钮');
        const suspectedBadge = host.document.querySelector('[data-governance-suggestion="dup_suspected_01"] [data-dedup-status]');
        assert.equal(suspectedBadge?.getAttribute('data-dedup-status'), 'suspected', '1500ms 慢响应完成前绝不得提前标为 confirmed');

        await firstRun;
        assert.ok(Date.now() - t0 >= 1400, '必须完整经历真实 ~1500ms 延迟后再放行终态');
        await wait(40);

        assert.equal(pollCalls >= 1, true, '必须轮询查重任务直到终态');
        assert.equal(host.main.dedupConfirmLoading, false);
        assert.ok(
            host.document.querySelector('[data-governance-confirm="dup_confirmed_01"]'),
            '后端任务成功后刷新列表，confirmed 卡片应解锁确认按钮'
        );
    } finally {
        postGate.resolve(jsonRes({ detail: { message: 'cleanup' } }, 500));
        host.dispose();
    }
});

test('QIN-619 generation 与 AbortController：关闭抽屉后旧在途加载自动中止且不污染状态', async () => {
    const signals = [];
    const gates = [];
    let capture = false;

    const fetchImpl = makeBaseFetch((target, init) => {
        if (target.includes('/api/asset-registry/governance/suggestions')) {
            if (!capture) {
                return Promise.resolve(jsonRes(makeSuggestionsPayload({ suspected: true, confirmed: false, revision: 0 })));
            }
            signals.push(init?.signal || null);
            const gate = deferred();
            gates.push(gate);
            return gate.promise;
        }
        return null;
    });

    const host = await createAssetViewHost({ embedded: true, tab: 'registry', fetchImpl });
    try {
        await host.main.gwCall('governance', 'openGovernanceDrawer', null);
        await wait(30);
        const { GW_DEFERRED } = await import(
            pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/state.js')).href
        );
        capture = true;

        // 发起第一个受控请求
        const load1 = GW_DEFERRED.governance.loadGovernanceSuggestions(true);
        await wait(20);
        assert.equal(signals.length, 1);
        assert.equal(signals[0]?.aborted, false);

        // 重入强制刷新：前一个请求必须被 abort
        const load2 = GW_DEFERRED.governance.loadGovernanceSuggestions(true);
        await wait(20);
        assert.equal(signals.length, 2);
        assert.equal(signals[0]?.aborted, true, '重入强制刷新必须中止前一次未完成的 AbortController');

        // 旧请求迟到返回错误/旧数据不得覆盖当前状态
        gates[0].resolve(jsonRes(makeSuggestionsPayload({ suspected: true, confirmed: false, revision: 1 })));
        await load1;
        assert.equal(host.main.governanceSuggestionsData, null, '过期代际响应不得写入当前状态');

        // 新请求完成
        gates[1].resolve(jsonRes(makeSuggestionsPayload({ suspected: false, confirmed: true, revision: 9 })));
        await load2;
        assert.equal(host.main.governanceSuggestionsData?.revision, 9, '最新代际响应必须正常生效');

        // 再发一次在途请求后立即关闭抽屉：必须中止在途请求并丢弃迟到返回
        const staleLoad = GW_DEFERRED.governance.loadGovernanceSuggestions(true);
        await wait(15);
        assert.equal(signals.length, 3);
        assert.equal(signals[2]?.aborted, false);
        const drawerHost = host.document.querySelector('[data-governance-drawer-host]');
        assert.ok(drawerHost);
        const listeners = drawerHost._listeners?.get('click') || [];
        for (const fn of listeners) {
            await fn.call(drawerHost, { target: drawerHost });
        }
        assert.equal(signals[2]?.aborted, true, '关闭抽屉必须中止仍在飞的治理加载请求');
        gates[2].resolve(jsonRes(makeSuggestionsPayload({ suspected: true, confirmed: true, revision: 99 })));
        await staleLoad;
        assert.notEqual(host.main.governanceSuggestionsData?.revision, 99, '关闭抽屉后的旧请求结果不得写回');
    } finally {
        gates.forEach(g => g.resolve(jsonRes(makeSuggestionsPayload())));
        host.dispose();
    }
});

test('QIN-619 写后刷新失败保护：确认成功但 GET 刷新失败时保留已成功结果并展示刷新提示', async () => {
    let getCount = 0;
    const initial = makeSuggestionsPayload({ suspected: false, confirmed: true, revision: 7 });

    const fetchImpl = makeBaseFetch((target, init) => {
        if (target.endsWith('/api/asset-registry/governance/suggestions/confirm') && init?.method === 'POST') {
            return Promise.resolve(jsonRes({ ok: true, revision: 8 }));
        }
        if (target.includes('/api/asset-registry/governance/suggestions')) {
            getCount += 1;
            if (getCount === 1) {
                return Promise.resolve(jsonRes(initial));
            }
            return Promise.resolve(jsonRes({ detail: { code: 'TRANSIENT_FAILURE', message: '清单刷新超时' } }, 503));
        }
        return null;
    });

    const host = await createAssetViewHost({ embedded: true, tab: 'registry', fetchImpl });
    try {
        await host.main.gwCall('governance', 'openGovernanceDrawer', null);
        await wait(60);

        await host.main.gwCall('governance', 'confirmGovernanceSuggestion', 'dup_confirmed_01');
        await wait(30);

        assert.ok(host.main.governanceSuggestionsData, '写后刷新失败时不得清空既有建议数据');
        assert.match(host.main.governanceRefreshNotice, /写入已提交，但刷新建议清单失败/, '必须设置明确写后刷新提示');
        const banner = host.document.querySelector('[data-governance-refresh-notice]');
        assert.ok(banner, '抽屉内必须渲染写后刷新提示条与重试按钮');
        assert.ok(banner.querySelector('[data-governance-retry]'), '提示条必须包含手动刷新重试按钮');
    } finally {
        host.dispose();
    }
});

test('QIN-619 task-center.js 与 i18n/task-center.js：支持 asset.dedup.confirm、paused 区分 cancelled 与选中态', async () => {
    const html = fs.readFileSync(path.join(ROOT, 'web/embeds/task-center.html'), 'utf8');
    const bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<script\b/i);
    assert.ok(bodyMatch, 'task-center.html 必须包含 body 结构');

    const i18nCode = fs.readFileSync(path.join(ROOT, 'web/js/modules/i18n/task-center.js'), 'utf8');
    const i18nStrings = {};
    const zhTaskCenter = {};

    const nowIso = new Date().toISOString();
    const dedupJob = {
        job_id: 'job_dedup_00000007',
        job_type: 'asset.dedup.confirm',
        name: '查重完整 SHA 确认',
        status: 'paused',
        progress: { processed: 3, total: 6, percent: 50 },
        created_at: nowIso,
        updated_at: nowIso,
        source: 'asset_registry',
    };
    const cancelledJob = {
        job_id: 'job_idx_00000002',
        job_type: 'asset.index',
        name: '索引重建',
        status: 'cancelled',
        progress: { processed: 1, total: 4, percent: 25 },
        created_at: nowIso,
        updated_at: nowIso,
        source: 'asset_registry',
    };

    const host = installHost({
        embedded: false,
        theme: 'dark',
        fetchImpl: (url) => {
            const target = String(url);
            if (target.includes('/api/asset-auth/status')) {
                return Promise.resolve(jsonRes({ principal: { role: 'admin', subject: 'qin619-admin' } }));
            }
            if (target.includes('/api/observability/overview') || target.includes('/api/observability/summary')) {
                return Promise.resolve(jsonRes({ total: 2, failed: 0, queue_depth: 1, latency_p95_ms: 12, data_status: 'ok' }));
            }
            if (target.includes('/api/observability/tasks')) {
                return Promise.resolve(jsonRes({ items: [dedupJob, cancelledJob], data_status: 'ok' }));
            }
            if (target.includes('/api/observability/events')) {
                return Promise.resolve(jsonRes({ items: [], data_status: 'ok' }));
            }
            if (target.includes('/api/observability/series')) {
                return Promise.resolve(jsonRes({ data_status: 'ok' }));
            }
            if (target.includes('/api/observability/health')) {
                return Promise.resolve(jsonRes({ items: [], data_status: 'ok' }));
            }
            if (target.includes('/api/observability/sources')) {
                return Promise.resolve(jsonRes({ items: [], data_status: 'ok' }));
            }
            if (target.includes('/api/observability/asset-volumes')) {
                return Promise.resolve(jsonRes({ items: [], data_status: 'ok' }));
            }
            if (target.includes('/api/asset-registry/workspace-jobs/job_dedup_00000007')) {
                return Promise.resolve(jsonRes({
                    job: dedupJob,
                    actions: { pause: false, resume: true, cancel: true, retry: false },
                }));
            }
            return Promise.resolve(jsonRes({ items: [], data_status: 'ok' }));
        },
    });

    try {
        host.document.body.innerHTML = bodyMatch[1];
        const dlg = host.document.querySelector('#taskDetail');
        if (dlg) {
            dlg.showModal = () => { dlg.open = true; };
            dlg.close = () => { dlg.open = false; };
        }
        const taskCenterCode = fs.readFileSync(path.join(ROOT, 'web/js/modules/task-center.js'), 'utf8');
        const StubNodeCtor = host.document.createElement('div').constructor;
        const sandbox = {
            document: host.document,
            window: globalThis,
            HTMLElement: StubNodeCtor,
            Node: StubNodeCtor,
            location: {
                href: 'http://127.0.0.1:2077/static/embeds/task-center.html?view=tasks',
                search: '?view=tasks',
                hash: '',
                origin: 'http://127.0.0.1:2077',
                pathname: '/static/embeds/task-center.html',
            },
            history: { replaceState() {} },
            localStorage: host.storage,
            sessionStorage: globalThis.sessionStorage,
            fetch: globalThis.fetch,
            URL,
            URLSearchParams,
            Intl,
            CSS: { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&') },
            navigator: globalThis.navigator,
            IntersectionObserver: globalThis.IntersectionObserver,
            requestAnimationFrame: globalThis.requestAnimationFrame,
            cancelAnimationFrame: globalThis.cancelAnimationFrame,
            setTimeout,
            clearTimeout,
            setInterval: () => 0,
            clearInterval() {},
            Event: globalThis.Event,
            CustomEvent: globalThis.CustomEvent,
            crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000619' },
            Workspace: {
                api: async (url, init) => {
                    const res = await globalThis.fetch(url, init);
                    const data = await res.json();
                    if (!res.ok) {
                        const err = new Error(data?.detail?.message || data?.message || 'HTTP ' + res.status);
                        err.status = res.status;
                        err.code = data?.detail?.code;
                        throw err;
                    }
                    return data;
                },
                escapeHtml: v => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
                dateTime: v => String(v || '—'),
                timeValue: v => String(v || '—'),
                icon: name => `<i data-lucide="${name}"></i>`,
                refreshIcons() {},
                syncTheme() {},
                navigate() {},
            },
            StudioI18n: {
                locale: () => 'zh-CN',
                register: entries => {
                    Object.assign(i18nStrings, entries || {});
                    Object.entries(entries || {}).forEach(([k, v]) => {
                        if (k.startsWith('taskCenter.')) {
                            zhTaskCenter[k.slice('taskCenter.'.length)] = v?.zh ?? '';
                        }
                    });
                },
                t: key => i18nStrings[key]?.zh ?? zhTaskCenter[String(key).replace(/^taskCenter\./, '')] ?? key,
            },
            Degradation: {
                fromError: err => ({ message: err?.message || 'error' }),
                fromPayload: () => null,
                renderBanner: () => '',
            },
            FloatingDismissal: { closeSurface: () => false },
            console,
        };
        sandbox.window = sandbox;
        sandbox.self = sandbox;
        sandbox.parent = sandbox;
        sandbox.top = sandbox;
        sandbox.addEventListener = (...args) => host.document.addEventListener(...args);
        sandbox.removeEventListener = (...args) => host.document.removeEventListener(...args);

        vm.runInNewContext(i18nCode, sandbox, { filename: 'web/js/modules/i18n/task-center.js' });
        assert.equal(typeof zhTaskCenter.longTaskDedup, 'string', 'i18n 必须定义 taskCenter.longTaskDedup');
        vm.runInNewContext(taskCenterCode, sandbox, { filename: 'web/js/modules/task-center.js' });
        await wait(80);

        const rows = host.document.querySelectorAll('#taskRows tr[data-job-row]');
        assert.equal(rows.length, 2, '任务表格必须渲染 dedup 与 index 两条任务行');

        const dedupRow = host.document.querySelector('tr[data-job-row="job_dedup_00000007"]');
        assert.ok(dedupRow, '必须渲染 asset.dedup.confirm 任务行');
        const pausedChip = dedupRow.querySelector('.status-chip.paused');
        assert.ok(pausedChip, 'paused 任务必须保留 .status-chip.paused 而非归为 cancelled');
        assert.match(pausedChip.textContent, /已暂停/);
        assert.equal(dedupRow.querySelector('.status-chip.cancelled'), null, 'paused 绝不能被误归类为 cancelled');

        const cancelledRow = host.document.querySelector('tr[data-job-row="job_idx_00000002"]');
        assert.ok(cancelledRow?.querySelector('.status-chip.cancelled'), 'cancelled 任务必须匹配 .status-chip.cancelled');

        const longTaskRow = host.document.querySelector('#longTaskProgress .longtask-kind.dedup');
        assert.ok(longTaskRow, '长时任务进度面板必须包含 .longtask-kind.dedup 类型标签');

        // 点击查看详情按钮应设置选中行 .is-selected + aria-selected=true
        const openBtn = dedupRow.querySelector('[data-open-task="job_dedup_00000007"]');
        assert.ok(openBtn, '任务行必须包含打开详情按钮');
        const clickListeners = host.document._listeners.get('click') || [];
        for (const handler of clickListeners) {
            await handler.call(host.document, { target: openBtn });
        }
        await wait(30);
        const updatedRow = host.document.querySelector('tr[data-job-row="job_dedup_00000007"]');
        assert.ok(updatedRow.classList.contains('is-selected'), '打开任务详情后对应任务行必须带有 .is-selected');
        assert.equal(updatedRow.getAttribute('aria-selected'), 'true');
    } finally {
        // restore installHost cleanup happens on next installHost or after() hook
    }
});

test('QIN-619 必改5：running 态关闭抽屉后重开必须自动恢复轮询并推进到 succeeded', async () => {
    let pollCount = 0;
    let currentPayload = makeSuggestionsPayload({ suspected: true, confirmed: false, revision: 20 });

    const fetchImpl = makeBaseFetch((target, init) => {
        if (target.endsWith('/api/asset-registry/governance/dedup-confirm-jobs') && init?.method === 'POST') {
            return Promise.resolve(jsonRes({
                job_id: 'job_dedup_reopen',
                job: { job_id: 'job_dedup_reopen', status: 'queued', progress: { processed: 0, total: 2, percent: 0 } },
                poll_hint: { url: '/api/asset-registry/workspace-jobs/job_dedup_reopen', interval_ms: 15 },
            }, 202));
        }
        if (target.includes('/api/asset-registry/workspace-jobs/job_dedup_reopen')) {
            pollCount += 1;
            if (pollCount < 3) {
                return Promise.resolve(jsonRes({
                    job: {
                        job_id: 'job_dedup_reopen',
                        job_type: 'asset.dedup.confirm',
                        status: 'running',
                        progress: { processed: 1, total: 2, percent: 50 },
                    },
                }));
            }
            currentPayload = makeSuggestionsPayload({ suspected: false, confirmed: true, revision: 21 });
            return Promise.resolve(jsonRes({
                job: {
                    job_id: 'job_dedup_reopen',
                    job_type: 'asset.dedup.confirm',
                    status: 'succeeded',
                    progress: { processed: 2, total: 2, percent: 100 },
                },
            }));
        }
        if (target.includes('/api/asset-registry/governance/suggestions')) {
            return Promise.resolve(jsonRes(currentPayload));
        }
        return null;
    });

    const host = await createAssetViewHost({ embedded: true, tab: 'registry', fetchImpl });
    try {
        await host.main.gwCall('governance', 'openGovernanceDrawer', null);
        await wait(50);
        const { GW_DEFERRED } = await import(
            pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/state.js')).href
        );

        void GW_DEFERRED.governance.startDedupConfirm({ pollIntervalMs: 15 });
        await wait(25);
        assert.equal(host.main.dedupConfirmJob?.status, 'running');

        // 抽屉在 running 期关闭，应当停止轮询并清理 loading 状态
        const drawerHost = host.document.querySelector('[data-governance-drawer-host]');
        assert.ok(drawerHost);
        const listeners = drawerHost._listeners?.get('click') || [];
        for (const fn of listeners) {
            await fn.call(drawerHost, { target: drawerHost });
        }
        assert.equal(host.main.governanceDrawerOpen, false);
        assert.equal(host.main.dedupConfirmLoading, false);
        const pollsAtClose = pollCount;
        await wait(45);
        assert.equal(pollCount, pollsAtClose, '抽屉关闭期间不得继续后台轮询');

        // 重开抽屉：必须自动恢复轮询直至 succeeded 并解锁确认按钮（绝不卡死在核验中）
        await GW_DEFERRED.governance.openGovernanceDrawer(null);
        await wait(80);
        assert.equal(host.main.dedupConfirmJob?.status, 'succeeded');
        assert.equal(host.main.dedupConfirmLoading, false);
        assert.ok(
            host.document.querySelector('[data-governance-confirm="dup_confirmed_01"]'),
            '重开恢复轮询完结后必须刷新出 confirmed 卡片并解锁确认按钮'
        );
    } finally {
        host.dispose();
    }
});

test('QIN-619 QA-AC15：治理分组与查重状态条必须绑定双主题令牌，严禁回落未定义的 --vault-raised 黑底', () => {
    const cssPath = path.join(ROOT, 'web/css/pages/asset-manager.css');
    const vaultPath = path.join(ROOT, 'web/css/pages/asset-vault.css');
    const cssText = fs.readFileSync(cssPath, 'utf-8');
    const vaultText = fs.readFileSync(vaultPath, 'utf-8');

    assert.match(vaultText, /--vault-surface:\s*var\(--gw-surface-panel\)/);
    assert.match(vaultText, /--vault-card:\s*var\(--gw-surface-card\)/);

    const govStart = cssText.indexOf('.governance-topbar-entry');
    const govEnd = cssText.indexOf('/* 【QIN-547】');
    assert.ok(govStart > 0 && govEnd > govStart, '必须定位到治理样式区段');
    const govSection = cssText.slice(govStart, govEnd);

    assert.equal(
        govSection.includes('--vault-raised'),
        false,
        '治理抽屉样式不得引用未定义的 --vault-raised（会导致浅色主题回落纯黑 #14171d）'
    );
    assert.equal(
        govSection.includes('--vault-text-secondary'),
        false,
        '治理抽屉正文不得引用未定义的 --vault-text-secondary（会导致浅色主题字色偏浅）'
    );
    assert.match(govSection, /\.governance-suggestion-group\s*\{[^}]*var\(--vault-surface/);
    assert.match(govSection, /\.governance-dedup-bar\s*\{[^}]*var\(--vault-card/);
});
