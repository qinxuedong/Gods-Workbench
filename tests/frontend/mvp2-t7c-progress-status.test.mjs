/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */
// MVP2-T7c（QIN-459 / #417）：任务进度步骤、失败原因（纯文本/防 XSS）、
// 重启后状态一致、API 失败明确错误态。全程 mock fetch，不引入任何第三方依赖。
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source = readFileSync(new URL('../../web/js/controllers/agent-tasks.js', import.meta.url), 'utf8');

function makeNode(tagName = 'div') {
    const listeners = new Map();
    const node = {
        tagName: tagName.toUpperCase(),
        children: [],
        dataset: {},
        disabled: false,
        hidden: false,
        value: '',
        textContent: '',
        className: '',
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(handler);
        },
        emit(type, event = {}) {
            const handler = listeners.get(type)?.at(-1);
            return handler?.({...event, target: this, preventDefault: event.preventDefault || (() => {})});
        },
        appendChild(child) {
            child.parentElement = this;
            this.children.push(child);
            return child;
        },
        // 真实 DOM 的插入语义，便于进度面板插入到状态行之后。
        insertBefore(child, reference) {
            child.parentElement = this;
            if (reference === null || reference === undefined) {
                this.children.push(child);
                return child;
            }
            const index = this.children.indexOf(reference);
            if (index === -1) this.children.push(child);
            else this.children.splice(index, 0, child);
            return child;
        },
        replaceChildren(...children) {
            this.children = [];
            for (const child of children) this.appendChild(child);
        },
        remove() {
            if (!this.parentElement) return;
            this.parentElement.children = this.parentElement.children.filter(child => child !== this);
            this.parentElement = null;
        },
        click() { this.clicked = true; },
        focus() {}
    };
    Object.defineProperty(node, 'firstElementChild', {get: () => node.children[0] || null});
    Object.defineProperty(node, 'nextSibling', {get: () => {
        if (!node.parentElement) return null;
        const index = node.parentElement.children.indexOf(node);
        return index >= 0 && index + 1 < node.parentElement.children.length ? node.parentElement.children[index + 1] : null;
    }});
    return node;
}

function jsonResponse(value) {
    return {
        ok: true,
        status: 200,
        headers: {get: name => name.toLowerCase() === 'content-type' ? 'application/json' : null},
        json: async () => value,
        blob: async () => new Blob([JSON.stringify(value)], {type: 'application/json'})
    };
}

function errorResponse(status, message) {
    return {
        ok: false,
        status,
        headers: {get: name => name.toLowerCase() === 'content-type' ? 'application/json' : null},
        json: async () => ({detail: {message}})
    };
}

function createHarness(fetchHandler, {storage = new Map(), jobMappings = new Map()} = {}) {
    const ids = [
        'agentTaskForm', 'agentTaskCreate', 'agentTaskLoad', 'agentTaskRunId', 'agentTaskStatus',
        'agentTaskProject', 'agentTaskGoal', 'agentTaskConfigProfile', 'agentTaskApplyConfiguration', 'agentProviderSelect', 'agentModelSelect',
        'agentTaskReview', 'agentTaskReviewScore', 'agentTaskCandidate',
        'agentTaskClarification', 'agentTaskQuestion', 'agentTaskExports', 'agentTraceStream'
    ];
    const nodes = new Map(ids.map(id => [id, makeNode(id === 'agentTaskForm' ? 'form' : 'div')]));
    const documentListeners = new Map();
    const timers = [];
    const clearedTimers = [];
    let timerId = 0;
    let randomIdCalls = 0;

    // 建立与真实页面一致的父子关系：状态行挂到表单，进度面板才能 insertBefore 到它后面。
    const form = nodes.get('agentTaskForm');
    form.appendChild(nodes.get('agentTaskStatus'));

    const document = {
        readyState: 'loading',
        body: makeNode('body'),
        getElementById: id => nodes.get(id) || null,
        createElement(tagName) { return makeNode(tagName); },
        addEventListener(type, handler) { documentListeners.set(type, handler); }
    };
    const window = {
        crypto: {randomUUID: () => `test-id-${++randomIdCalls}`},
        setInterval(callback, delay) {
            const timer = {id: ++timerId, callback, delay};
            timers.push(timer);
            return timer.id;
        },
        clearInterval(id) { clearedTimers.push(id); },
        confirm: () => true,
        localStorage: {
            getItem(key) { return storage.has(key) ? storage.get(key) : null; },
            setItem(key, value) { storage.set(key, String(value)); },
            removeItem(key) { storage.delete(key); }
        }
    };
    const fetch = async (path, options = {}) => {
        const jobMatch = path.match(/^\/api\/jobs\/([^/?]+)$/);
        if (jobMatch && (options.method || 'GET') === 'GET') {
            const jobId = decodeURIComponent(jobMatch[1]);
            let runId;
            if (jobMappings.has(jobId)) runId = jobMappings.get(jobId);
            else if (jobId.startsWith('job_agent_')) runId = jobId.slice('job_agent_'.length);
            if (runId === null) return errorResponse(404, '任务不存在或当前主体不可访问');
            if (typeof runId === 'string' && runId) {
                return jsonResponse({job_id: jobId, state: 'accepted', poll_hint: path, agent_status: 'queued', run_id: runId, version: 1, stage: null});
            }
        }
        return fetchHandler(path, options);
    };

    runInNewContext(source, {document, window, fetch, URL: {createObjectURL: () => 'blob:x', revokeObjectURL: () => {}}, URLSearchParams, Blob});

    return {
        nodes, timers,
        async init() { await documentListeners.get('DOMContentLoaded')(); },
        async load(jobId) {
            nodes.get('agentTaskRunId').value = jobId;
            await nodes.get('agentTaskLoad').emit('click');
        }
    };
}

// 通用基础请求：项目列表、配置、候选/澄清/导出（默认空）。
function baseline(path) {
    if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
    if (path === '/api/agent/config') return jsonResponse({ready: false});
    if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
    if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
    if (path.endsWith('/reviews')) return jsonResponse({reviews: []});
    return undefined;
}

function event(type, payload) {
    return {event_id: `evt-${type}`, version: payload.version, stage: payload.stage ?? null,
        event_type: payload.event_type, summary: payload.summary};
}

function progressPanel(h) {
    const panel = h.nodes.get('agentTaskForm').children.find(c => c.dataset.t7cProgress === 'host');
    assert.ok(panel, '任务进度面板应已挂载到表单');
    return panel;
}

// 捕获进度面板的可比较状态（用于重启一致性）。
function captureProgress(h) {
    const panel = progressPanel(h);
    const failure = panel.children[3];
    return {
        current: panel.children[1].textContent,
        steps: panel.children[2].children.map(row => row.dataset.stepState),
        failureHidden: failure.hidden,
        reason: failure.children[1].textContent,
        noticeHidden: panel.children[4].hidden,
        notice: panel.children[4].textContent
    };
}

async function waitFor(predicate) {
    for (let index = 0; index < 30; index += 1) {
        if (predicate()) return;
        await new Promise(resolve => setImmediate(resolve));
    }
    assert.fail('等待本地模拟请求超时');
}

test('运行中任务显示当前步骤与六步完成状态', async () => {
    const h = createHarness(async path => {
        const base = baseline(path);
        if (base !== undefined) return base;
        if (path === '/api/agent/runs/run-progress') {
            return jsonResponse({version: 3, status: 'running', stage: 'full_script'});
        }
        if (path.startsWith('/api/agent/runs/run-progress/events?')) {
            return jsonResponse({events: [
                event(1, {version: 1, stage: 'brief', event_type: 'run.started', summary: '简报完成'}),
                event(2, {version: 2, stage: 'outline_characters', event_type: 'stage.gate.passed', summary: '大纲人物完成'}),
                event(3, {version: 3, stage: 'full_script', event_type: 'attempt.queued', summary: '完整剧本进行中'})
            ], cursor: null});
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await h.init();
    await h.load('job_agent_run-progress');

    const panel = progressPanel(h);
    assert.match(panel.children[1].textContent, /当前步骤：完整剧本（3\/6）/);
    const rows = panel.children[2].children;
    assert.equal(rows.length, 6);
    assert.deepEqual(rows.map(row => row.dataset.stepState), ['done', 'done', 'active', 'pending', 'pending', 'pending']);
    assert.match(rows[0].textContent, /已完成 · 简报/);
    assert.match(rows[2].textContent, /进行中 · 完整剧本/);
    assert.match(rows[5].textContent, /待处理 · 交付核验/);
});

test('失败原因以纯文本渲染，HTML 注入不被解析（防 XSS）', async () => {
    const payload = '<img src=x onerror="alert(1)"><script>bad()</script>';
    const h = createHarness(async path => {
        const base = baseline(path);
        if (base !== undefined) return base;
        if (path === '/api/agent/runs/run-xss') {
            return jsonResponse({version: 5, status: 'failed', stage: 'full_script', failure_reason: payload});
        }
        if (path.startsWith('/api/agent/runs/run-xss/events?')) {
            return jsonResponse({events: [], cursor: null});
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await h.init();
    await h.load('job_agent_run-xss');

    const panel = progressPanel(h);
    const failure = panel.children[3];
    assert.equal(failure.hidden, false, '失败状态应显示失败卡');
    const reason = failure.children[1];
    // 纯文本：完整保留原始字符串，且不产生任何子元素（innerHTML 才会解析出 img/script）。
    assert.equal(reason.textContent, payload);
    assert.equal(reason.children.length, 0);
    // 失败步骤被标记为已失败。
    assert.equal(panel.children[2].children[2].dataset.stepState, 'failed');
    assert.match(panel.children[1].textContent, /任务在步骤「完整剧本」失败/);
});

test('run 无显式失败字段时，失败原因取失败事件摘要', async () => {
    const h = createHarness(async path => {
        const base = baseline(path);
        if (base !== undefined) return base;
        if (path === '/api/agent/runs/run-evfail-reason') {
            return jsonResponse({version: 4, status: 'failed', stage: 'scene_script'});
        }
        if (path.startsWith('/api/agent/runs/run-evfail-reason/events?')) {
            return jsonResponse({events: [
                event(1, {version: 3, stage: 'scene_script', event_type: 'stage.failed', summary: '分场校验失败：缺少场景标题'})
            ], cursor: null});
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await h.init();
    await h.load('job_agent_run-evfail-reason');

    const failure = progressPanel(h).children[3];
    assert.equal(failure.children[1].textContent, '分场校验失败：缺少场景标题');
});

test('成功任务显示全部步骤已完成', async () => {
    const h = createHarness(async path => {
        const base = baseline(path);
        if (base !== undefined) return base;
        if (path === '/api/agent/runs/run-done') return jsonResponse({version: 9, status: 'succeeded', stage: 'delivery_check'});
        if (path.startsWith('/api/agent/runs/run-done/events?')) return jsonResponse({events: [], cursor: null});
        throw new Error(`未预期的本地请求：${path}`);
    });

    await h.init();
    await h.load('job_agent_run-done');

    const panel = progressPanel(h);
    assert.match(panel.children[1].textContent, /已完成全部 6 个步骤/);
    assert.deepEqual(panel.children[2].children.map(row => row.dataset.stepState), Array(6).fill('done'));
    assert.equal(panel.children[3].hidden, true);
});

test('重启后重拉同一 session，进度步骤与失败状态与之前一致', async () => {
    const storage = new Map();
    const jobId = 'job_agent_run-restart';
    const makeHandler = () => async path => {
        const base = baseline(path);
        if (base !== undefined) return base;
        if (path === '/api/agent/runs/run-restart') {
            return jsonResponse({version: 5, status: 'failed', stage: 'full_script', failure_reason: '模型返回格式不合规'});
        }
        if (path.startsWith('/api/agent/runs/run-restart/events?')) {
            return jsonResponse({events: [
                event(1, {version: 1, stage: 'brief', event_type: 'run.started', summary: '简报完成'}),
                event(2, {version: 2, stage: 'outline_characters', event_type: 'stage.gate.passed', summary: '大纲人物完成'}),
                event(3, {version: 5, stage: 'full_script', event_type: 'stage.failed', summary: '模型返回格式不合规'})
            ], cursor: null});
        }
        throw new Error(`未预期的本地请求：${path}`);
    };

    const before = createHarness(makeHandler(), {storage});
    await before.init();
    await before.load(jobId);
    const snapshot = captureProgress(before);
    assert.equal(snapshot.failureHidden, false);
    assert.equal(snapshot.reason, '模型返回格式不合规');

    // 模拟重启：同一 localStorage，新建上下文，启动时自动恢复同一 job。
    const after = createHarness(makeHandler(), {storage});
    await after.init();

    assert.deepEqual(captureProgress(after), snapshot);
    assert.equal(after.nodes.get('agentTaskRunId').value, jobId);
});

test('事件接口失败时进度面板显示错误态，已读步骤不白屏', async () => {
    let eventReads = 0;
    const h = createHarness(async path => {
        const base = baseline(path);
        if (base !== undefined) return base;
        if (path === '/api/agent/runs/run-step-err') {
            return jsonResponse({version: 2, status: 'running', stage: 'outline_characters'});
        }
        if (path.startsWith('/api/agent/runs/run-step-err/events?')) {
            eventReads += 1;
            if (eventReads === 1) {
                return jsonResponse({events: [
                    event(1, {version: 1, stage: 'brief', event_type: 'run.started', summary: '简报完成'})
                ], cursor: null});
            }
            return errorResponse(503, '事件服务暂时不可用');
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await h.init();
    await h.load('job_agent_run-step-err');
    assert.equal(progressPanel(h).children[4].hidden, true, '首次读取不应有错误态');

    // 触发一次轮询，事件接口返回 503。
    const poll = h.timers.at(-1).callback();
    await waitFor(() => eventReads === 2);
    await poll;

    const panel = progressPanel(h);
    const notice = panel.children[4];
    assert.equal(notice.hidden, false, '事件接口失败应显示明确错误态');
    assert.match(notice.textContent, /任务步骤暂时不可读/);
    assert.equal(notice.dataset.gwDegradation, 'pending');
    // 已读取的六步骨架仍在，不白屏。
    assert.equal(panel.children[2].children.length, 6);
});

test('主状态接口失败时进度面板显示错误态，不白屏', async () => {
    let runReads = 0;
    const h = createHarness(async path => {
        const base = baseline(path);
        if (base !== undefined) return base;
        if (path === '/api/agent/runs/run-main-err') {
            runReads += 1;
            if (runReads === 1) return jsonResponse({version: 1, status: 'running', stage: 'brief'});
            return errorResponse(500, '状态服务内部错误');
        }
        if (path.startsWith('/api/agent/runs/run-main-err/events?')) {
            return jsonResponse({events: [
                event(1, {version: 1, stage: 'brief', event_type: 'run.started', summary: '简报进行中'})
            ], cursor: null});
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await h.init();
    await h.load('job_agent_run-main-err');

    const poll = h.timers.at(-1).callback();
    await waitFor(() => runReads === 2);
    await poll;

    const panel = progressPanel(h);
    const notice = panel.children[4];
    assert.equal(notice.hidden, false, '主状态接口失败应显示明确错误态');
    assert.match(notice.textContent, /任务状态暂时不可读/);
});
