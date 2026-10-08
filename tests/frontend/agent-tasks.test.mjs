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

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((done, fail) => {
        resolve = done;
        reject = fail;
    });
    return {promise, resolve, reject};
}

function createHarness(fetchHandler, {storage = new Map(), storageUnavailable = false, jobMappings = new Map()} = {}) {
    const ids = [
        'agentTaskForm', 'agentTaskCreate', 'agentTaskLoad', 'agentTaskRunId', 'agentTaskStatus',
        'agentTaskProject', 'agentTaskGoal', 'agentTaskConfigProfile', 'agentTaskApplyConfiguration', 'agentProviderSelect', 'agentModelSelect',
        'agentTaskReview', 'agentTaskReviewScore', 'agentTaskCandidate',
        'agentTaskClarification', 'agentTaskQuestion', 'agentTaskExports', 'agentTraceStream'
    ];
    const nodes = new Map(ids.map(id => [id, makeNode(id === 'agentTaskForm' ? 'form' : 'div')]));
    const documentListeners = new Map();
    const createdElements = [];
    const calls = [];
    const timers = [];
    const clearedTimers = [];
    const blobs = [];
    const revokedUrls = [];
    let timerId = 0;
    let randomIdCalls = 0;

    const document = {
        readyState: 'loading',
        body: makeNode('body'),
        getElementById: id => nodes.get(id) || null,
        createElement(tagName) {
            const node = makeNode(tagName);
            createdElements.push(node);
            return node;
        },
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
        confirm: () => true
    };
    const localStorage = {
        getItem(key) { return storage.has(key) ? storage.get(key) : null; },
        setItem(key, value) { storage.set(key, String(value)); },
        removeItem(key) { storage.delete(key); }
    };
    if (storageUnavailable) {
        Object.defineProperty(window, 'localStorage', {get() { throw new Error('存储不可用'); }});
    } else {
        window.localStorage = localStorage;
    }
    const urlApi = {
        createObjectURL(blob) {
            blobs.push(blob);
            return `blob:agent-tasks/${blobs.length}`;
        },
        revokeObjectURL(url) { revokedUrls.push(url); }
    };
    const fetch = async (path, options = {}) => {
        calls.push({path, options});
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

    runInNewContext(source, {document, window, fetch, URL: urlApi, URLSearchParams, Blob});

    return {
        nodes, calls, timers, clearedTimers, blobs, revokedUrls, createdElements, storage,
        get randomIdCalls() { return randomIdCalls; },
        async init() { await documentListeners.get('DOMContentLoaded')(); },
        async load(runId) {
            nodes.get('agentTaskRunId').value = runId;
            await nodes.get('agentTaskLoad').emit('click');
        },
        async create({projectId = 'project-test', goal = '创建测试任务'} = {}) {
            nodes.get('agentTaskProject').value = projectId;
            nodes.get('agentTaskGoal').value = goal;
            await nodes.get('agentTaskForm').emit('submit');
        }
    };
}

function eventQuery(call) {
    const search = call.path.split('?')[1] || '';
    return new URLSearchParams(search);
}

async function waitFor(predicate) {
    for (let index = 0; index < 30; index += 1) {
        if (predicate()) return;
        await new Promise(resolve => setImmediate(resolve));
    }
    assert.fail('等待本地模拟请求超时');
}

test('切换任务会隔离事件 cursor、ID、version，并丢弃旧请求迟到响应', async () => {
    let runAEvents = 0;
    let runBEvents = 0;
    const staleResponse = deferred();
    const harness = createHarness(async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        if (path === '/api/agent/runs/run-a') return jsonResponse({version: 20, status: 'queued'});
        if (path === '/api/agent/runs/run-b') return jsonResponse({version: 2, status: 'queued'});
        if (path.startsWith('/api/agent/runs/run-a/events?')) {
            runAEvents += 1;
            if (runAEvents === 1) return jsonResponse({
                events: [{event_id: 'reused-id', version: 25, summary: '任务 A'}], cursor: 'cursor-a'
            });
            return staleResponse.promise;
        }
        if (path.startsWith('/api/agent/runs/run-b/events?')) {
            runBEvents += 1;
            return jsonResponse(runBEvents === 1
                ? {events: [{event_id: 'reused-id', version: 3, summary: '任务 B'}], cursor: 'cursor-b'}
                : {events: [], cursor: 'cursor-b-next'});
        }
        if (/\/(candidate|clarifications|reviews)$/.test(path)) {
            return jsonResponse(path.endsWith('/clarifications') ? {questions: [], is_pending: false} : {candidate: null, reviews: []});
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await harness.init();
    await harness.load('job_agent_run-a');
    assert.ok(harness.calls.some(call => call.path === '/api/agent/runs/run-a'), JSON.stringify(harness.calls.map(call => call.path)));
    assert.ok(harness.calls.some(call => call.path.startsWith('/api/agent/runs/run-a/events?')), JSON.stringify({calls: harness.calls.map(call => call.path), status: harness.nodes.get('agentTaskStatus').textContent}));
    const firstAQuery = eventQuery(harness.calls.find(call => call.path.startsWith('/api/agent/runs/run-a/events?')));
    assert.equal(firstAQuery.get('after_version'), '0');
    assert.equal(firstAQuery.has('cursor'), false);

    const oldPoll = harness.timers.at(-1).callback();
    await waitFor(() => runAEvents === 2);
    await harness.load('job_agent_run-b');

    const bCalls = harness.calls.filter(call => call.path.startsWith('/api/agent/runs/run-b/events?'));
    const firstBQuery = eventQuery(bCalls[0]);
    assert.equal(firstBQuery.get('after_version'), '0');
    assert.equal(firstBQuery.has('cursor'), false);

    staleResponse.resolve(jsonResponse({
        events: [{event_id: 'late-old-id', version: 99, summary: '迟到的任务 A'}], cursor: 'cursor-a-late'
    }));
    await oldPoll;
    const traceRows = harness.nodes.get('agentTraceStream').children;
    assert.equal(traceRows.length, 1);
    assert.match(traceRows[0].textContent, /任务 B/);

    await harness.timers.at(-1).callback();
    const nextBQuery = eventQuery(harness.calls.filter(call => call.path.startsWith('/api/agent/runs/run-b/events?'))[1]);
    assert.equal(nextBQuery.get('after_version'), '3');
    assert.equal(nextBQuery.get('cursor'), 'cursor-b');
});

test('同一任务并发刷新只接受最新主响应、子响应与轮询错误', async () => {
    let runReads = 0;
    let eventReads = 0;
    let candidateReads = 0;
    let clarificationReads = 0;
    let reviewReads = 0;
    const oldRunResponse = deferred();
    const oldEventsResponse = deferred();
    const oldCandidateResponse = deferred();
    const oldClarificationResponse = deferred();
    const oldReviewsResponse = deferred();
    const oldPollError = deferred();
    const currentCandidate = content => ({
        is_current: true,
        can_decide: true,
        content,
        review: {overall_score: 8}
    });
    const harness = createHarness(async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        if (path === '/api/agent/runs/run-same') {
            runReads += 1;
            if (runReads === 1) return jsonResponse({version: 1, status: 'queued', stage: 'initial'});
            if (runReads === 2) return oldRunResponse.promise;
            if (runReads === 3) return jsonResponse({version: 3, status: 'queued', stage: 'base-latest'});
            if (runReads === 4) return jsonResponse({version: 4, status: 'queued', stage: 'children-old'});
            if (runReads === 5) return jsonResponse({version: 5, status: 'queued', stage: 'children-latest'});
            if (runReads === 6) return oldPollError.promise;
            if (runReads === 7) return jsonResponse({version: 6, status: 'queued', stage: 'after-error-latest'});
        }
        if (path.startsWith('/api/agent/runs/run-same/events?')) {
            eventReads += 1;
            if (eventReads === 1) return jsonResponse({events: [], cursor: 'cursor-initial'});
            if (eventReads === 2) return jsonResponse({events: [], cursor: 'cursor-base-latest'});
            if (eventReads === 3) return oldEventsResponse.promise;
            if (eventReads === 4) return jsonResponse({
                events: [{event_id: 'latest-event', version: 6, summary: '最新事件'}], cursor: 'cursor-latest'
            });
            if (eventReads === 5) return jsonResponse({events: [], cursor: 'cursor-after-error'});
        }
        if (path.endsWith('/candidate')) {
            candidateReads += 1;
            if (candidateReads === 1 || candidateReads === 2) return jsonResponse({candidate: null});
            if (candidateReads === 3) return oldCandidateResponse.promise;
            if (candidateReads >= 4) return jsonResponse({candidate: currentCandidate('最新候选')});
        }
        if (path.endsWith('/clarifications')) {
            clarificationReads += 1;
            if (clarificationReads === 3) return oldClarificationResponse.promise;
            return jsonResponse({questions: [], is_pending: false});
        }
        if (path.endsWith('/reviews')) {
            reviewReads += 1;
            if (reviewReads === 3) return oldReviewsResponse.promise;
            return jsonResponse({reviews: []});
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await harness.init();
    await harness.load('job_agent_run-same');
    const poll = harness.timers.at(-1).callback;

    const oldBasePoll = poll();
    await waitFor(() => runReads === 2);
    await poll();
    oldRunResponse.resolve(jsonResponse({version: 2, status: 'waiting_review', stage: 'base-old-late'}));
    await oldBasePoll;
    assert.match(harness.nodes.get('agentTaskStatus').textContent, /base-latest · 版本 3/);
    assert.equal(eventReads, 2, '旧主响应失效后不应再发起它的子请求');

    const oldChildrenPoll = poll();
    await waitFor(() => runReads === 4 && eventReads === 3 && candidateReads === 3 && clarificationReads === 3 && reviewReads === 3);
    await poll();
    oldEventsResponse.resolve(jsonResponse({
        events: [{event_id: 'old-event', version: 99, summary: '迟到旧事件'}], cursor: 'cursor-old'
    }));
    oldCandidateResponse.resolve(jsonResponse({candidate: currentCandidate('旧候选')}));
    oldClarificationResponse.resolve(jsonResponse({
        is_pending: true,
        questions: [{question_id: 'old-question', text: '迟到旧问题'}]
    }));
    oldReviewsResponse.resolve(jsonResponse({reviews: [{
        review: {review_id: 'old-review', artifact: {version_id: 'old-artifact'}},
        approval: {decision: 'approve'}
    }]}));
    await oldChildrenPoll;

    assert.match(harness.nodes.get('agentTaskStatus').textContent, /children-latest · 版本 5/);
    assert.equal(harness.nodes.get('agentTaskCandidate').textContent, '最新候选');
    assert.equal(harness.nodes.get('agentTaskClarification').hidden, true);
    assert.equal(harness.nodes.get('agentTaskExports').children.length, 0);
    assert.deepEqual(harness.nodes.get('agentTraceStream').children.map(row => row.textContent), ['任务 · 最新事件']);

    const oldErrorPoll = poll();
    await waitFor(() => runReads === 6);
    await poll();
    oldPollError.reject(new Error('迟到轮询错误'));
    await oldErrorPoll;
    assert.match(harness.nodes.get('agentTaskStatus').textContent, /after-error-latest · 版本 6/);

    const lastEventCall = harness.calls.filter(call => call.path.startsWith('/api/agent/runs/run-same/events?')).at(-1);
    const latestQuery = eventQuery(lastEventCall);
    assert.equal(latestQuery.get('after_version'), '6');
    assert.equal(latestQuery.get('cursor'), 'cursor-latest');
});

test('JSON 导出按 Blob 读取 application/json 响应', async () => {
    let jsonReads = 0;
    let blobReads = 0;
    const artifact = {version_id: 'artifact-v1'};
    const harness = createHarness(async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        if (path === '/api/agent/runs/run-export') return jsonResponse({version: 4, status: 'succeeded'});
        if (path.startsWith('/api/agent/runs/run-export/events?')) return jsonResponse({events: [], cursor: null});
        if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
        if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
        if (path.endsWith('/reviews')) return jsonResponse({reviews: [{
            review: {review_id: 'review-1', artifact},
            approval: {decision: 'approve'},
            export_formats: ['exact-json']
        }]});
        if (path.includes('/export?')) {
            return {
                ok: true,
                status: 200,
                headers: {get: () => 'application/json'},
                async json() { jsonReads += 1; return {content: 'result'}; },
                async blob() {
                    blobReads += 1;
                    return new Blob(['{"content":"result"}'], {type: 'application/json'});
                }
            };
        }
        throw new Error(`未预期的本地请求：${path}`);
    });

    await harness.init();
    await harness.load('job_agent_run-export');
    assert.ok(harness.calls.some(call => call.path === '/api/agent/runs/run-export'), JSON.stringify(harness.calls.map(call => call.path)));
    assert.ok(harness.calls.some(call => call.path.endsWith('/reviews')), JSON.stringify({calls: harness.calls.map(call => call.path), status: harness.nodes.get('agentTaskStatus').textContent}));
    const exportGroup = harness.nodes.get('agentTaskExports').children[0];
    const jsonButton = exportGroup.children.find(child => child.textContent === '导出 JSON');
    await jsonButton.emit('click');

    assert.equal(jsonReads, 0);
    assert.equal(blobReads, 1);
    assert.equal(harness.blobs.length, 1);
    assert.equal(await harness.blobs[0].text(), '{"content":"result"}');
    const link = harness.createdElements.find(element => element.tagName === 'A');
    assert.equal(link.download, 'agent-artifact-v1.json');
    assert.equal(link.clicked, true);
    assert.deepEqual(harness.revokedUrls, ['blob:agent-tasks/1']);
    assert.equal(harness.nodes.get('agentTaskStatus').textContent.startsWith('导出失败：'), false);
});

test('导出按钮严格跟随服务端能力并绑定精确 review 与 artifact version', async () => {
    const reviews = [
        {
            review: {review_id: 'review-script', artifact: {version_id: 'version-script', stage: 'full_script'}},
            approval: null,
            export_formats: ['markdown']
        },
        {
            review: {review_id: 'review-storyboard', artifact: {version_id: 'version-storyboard', stage: 'storyboard_text'}},
            approval: {decision: 'approve'},
            export_formats: ['csv']
        },
        {
            review: {review_id: 'review-delivery', artifact: {version_id: 'version-delivery', stage: 'delivery_check'}},
            approval: null,
            export_formats: ['exact-json']
        },
        {
            review: {review_id: 'review-stale', artifact: {version_id: 'version-stale', stage: 'full_script'}},
            approval: null,
            export_formats: []
        },
        {
            review: {review_id: 'review-no-capability', artifact: {version_id: 'version-no-capability', stage: 'delivery_check'}},
            approval: {decision: 'approve'}
        }
    ];
    const harness = createHarness(async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        if (path === '/api/agent/runs/run-export-map') return jsonResponse({version: 7, status: 'succeeded'});
        if (path.startsWith('/api/agent/runs/run-export-map/events?')) return jsonResponse({events: [], cursor: null});
        if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
        if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
        if (path.endsWith('/reviews')) return jsonResponse({reviews});
        if (path.includes('/export?')) return {
            ok: true,
            status: 200,
            headers: {get: () => 'application/json'},
            json: async () => ({unexpected: 'parsed as JSON'}),
            blob: async () => new Blob([path], {type: 'application/json'})
        };
        throw new Error(`未预期的本地请求：${path}`);
    });

    await harness.init();
    await harness.load('job_agent_run-export-map');
    const groups = harness.nodes.get('agentTaskExports').children;
    assert.equal(groups.length, 3, '空能力和缺失能力的 item 不应生成导出组');
    assert.deepEqual(groups.map(group => group.children.slice(1).map(button => button.textContent)), [
        ['导出 Markdown'], ['导出 CSV'], ['导出 JSON']
    ]);
    assert.match(groups[0].children[0].textContent, /version-script/);

    const expected = [
        ['markdown', 'review-script', 'version-script'],
        ['csv', 'review-storyboard', 'version-storyboard'],
        ['exact-json', 'review-delivery', 'version-delivery']
    ];
    for (let index = 0; index < expected.length; index += 1) {
        await groups[index].children[1].emit('click');
    }
    const exportCalls = harness.calls.filter(call => call.path.includes('/export?'));
    assert.equal(exportCalls.length, expected.length);
    for (let index = 0; index < expected.length; index += 1) {
        const query = eventQuery(exportCalls[index]);
        assert.equal(query.get('format'), expected[index][0]);
        assert.equal(query.get('review_id'), expected[index][1]);
        assert.equal(query.get('artifact_version_id'), expected[index][2]);
    }
});

test('重新打开页面只凭本地保存的稳定 job_id 解析 run_id 并恢复', async () => {
    const storage = new Map();
    const jobId = 'stable-agent-job-17';
    const runId = 'run-after-restart';
    const fetchHandler = async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        if (path === `/api/agent/runs/${runId}`) return jsonResponse({version: 4, status: 'running'});
        if (path.startsWith(`/api/agent/runs/${runId}/events?`)) return jsonResponse({events: [], cursor: null});
        if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
        if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
        if (path.endsWith('/reviews')) return jsonResponse({reviews: []});
        throw new Error(`未预期的本地请求：${path}`);
    };

    const jobMappings = new Map([[jobId, runId]]);
    const beforeClose = createHarness(fetchHandler, {storage, jobMappings});
    await beforeClose.init();
    await beforeClose.load(jobId);
    assert.deepEqual([...storage], [['gw.agentTasks.lastJobId', jobId]]);

    const afterReopen = createHarness(fetchHandler, {storage, jobMappings});
    await afterReopen.init();

    const recoveredJob = afterReopen.calls.find(call => call.path === `/api/jobs/${jobId}`);
    const recoveredStatus = afterReopen.calls.find(call => call.path === `/api/agent/runs/${runId}`);
    assert.ok(recoveredJob, '启动时应先通过通用 job API 解析内部 run_id');
    assert.equal(recoveredJob.options.method || 'GET', 'GET');
    assert.equal(recoveredJob.options.credentials, 'same-origin');
    assert.ok(recoveredStatus, '解析后应读取 Agent 详情');
    assert.equal(afterReopen.nodes.get('agentTaskRunId').value, jobId);
    assert.match(afterReopen.nodes.get('agentTaskStatus').textContent, new RegExp(`已恢复任务 ${jobId}`));
    assert.ok(afterReopen.timers.some(timer => timer.delay === 2000), '运行中的任务应继续轮询');
    assert.ok(afterReopen.calls.every(call => (call.options.method || 'GET') === 'GET'), '恢复过程只能发 GET');
    assert.deepEqual([...storage], [['gw.agentTasks.lastJobId', jobId]], '本地只应保存稳定 job_id');
});

test('恢复任务返回 404 时清除失效编号', async () => {
    const jobId = 'job_agent_stale-job';
    const storage = new Map([['gw.agentTasks.lastJobId', jobId]]);
    const harness = createHarness(async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        if (path === '/api/agent/runs/stale-job') return errorResponse(404, '任务不存在');
        throw new Error(`未预期的本地请求：${path}`);
    }, {storage, jobMappings: new Map([[jobId, null]])});

    await harness.init();

    assert.equal(storage.has('gw.agentTasks.lastJobId'), false);
    assert.equal(harness.nodes.get('agentTaskRunId').value, '');
    assert.match(harness.nodes.get('agentTaskStatus').textContent, /清除失效编号/);
    assert.equal(harness.timers.length, 0);
});

test('localStorage 不可用时仍可手动读取任务', async () => {
    const harness = createHarness(async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        if (path === '/api/agent/runs/manual-run') return jsonResponse({version: 1, status: 'succeeded'});
        if (path.startsWith('/api/agent/runs/manual-run/events?')) return jsonResponse({events: [], cursor: null});
        if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
        if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
        if (path.endsWith('/reviews')) return jsonResponse({reviews: []});
        throw new Error(`未预期的本地请求：${path}`);
    }, {storageUnavailable: true});

    await harness.init();
    await harness.load('job_agent_manual-run');

    assert.ok(harness.calls.some(call => call.path === '/api/jobs/job_agent_manual-run'));
    assert.ok(harness.calls.some(call => call.path === '/api/agent/runs/manual-run'));
    assert.equal(harness.nodes.get('agentTaskRunId').value, 'job_agent_manual-run');
    assert.match(harness.nodes.get('agentTaskStatus').textContent, /已读取任务 job_agent_manual-run/);
});

test('POST 已受理但状态 GET 失败后只恢复原任务并保留同一幂等请求', async () => {
    const storage = new Map();
    const createPayloads = [];
    let statusReads = 0;
    const harness = createHarness(async (path, options) => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: true, profiles: [
            {config_snapshot_id: 'profile-stable-v1', provider_id: 'provider-safe', model: 'model-safe', mode: 'approval'}
        ]});
        if (path === '/api/agent/runs' && options.method === 'POST') {
            createPayloads.push(JSON.parse(options.body));
            return jsonResponse({job_id: 'job_agent_accepted-run', run_id: 'accepted-run', version: 1});
        }
        if (path === '/api/agent/runs/accepted-run') {
            statusReads += 1;
            if (statusReads === 1) return errorResponse(503, '状态服务暂时不可用');
            return jsonResponse({version: 2, status: 'queued'});
        }
        if (path.startsWith('/api/agent/runs/accepted-run/events?')) return jsonResponse({events: [], cursor: null});
        if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
        if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
        if (path.endsWith('/reviews')) return jsonResponse({reviews: []});
        throw new Error(`未预期的本地请求：${path}`);
    }, {storage});

    await harness.init();
    await harness.create();
    assert.equal(createPayloads.length, 1);
    const idempotencyKey = createPayloads[0].idempotency_key;
    assert.ok(idempotencyKey);
    assert.equal(createPayloads[0].config_snapshot_id, 'profile-stable-v1');
    assert.equal(createPayloads[0].provider_id, 'provider-safe');
    assert.equal(createPayloads[0].model, 'model-safe');
    assert.equal(createPayloads[0].mode, 'approval');
    assert.deepEqual(createPayloads[0].input_artifact_refs, []);
    assert.equal(Object.hasOwn(createPayloads[0], 'job_id'), false);
    assert.equal(storage.get('gw.agentTasks.lastJobId'), 'job_agent_accepted-run');
    assert.match(harness.nodes.get('agentTaskStatus').textContent, /任务已受理：job_agent_accepted-run.*不会创建新任务/);
    const keyCountAfterAcceptance = harness.randomIdCalls;

    await harness.create();

    assert.equal(createPayloads.length, 1, '已有 accepted job 时重试不得再 POST 新任务');
    assert.equal(statusReads, 2, '重试应 GET 同一个 accepted job');
    assert.equal(harness.nodes.get('agentTaskRunId').value, 'job_agent_accepted-run');
    assert.match(harness.nodes.get('agentTaskStatus').textContent, /任务状态已恢复：job_agent_accepted-run/);
    assert.equal(harness.randomIdCalls, keyCountAfterAcceptance, '重试恢复任务时不得生成新的幂等键或请求 ID');
    assert.equal(createPayloads[0].idempotency_key, idempotencyKey);
});

test('ready=false 状态说明同时覆盖模型配置和单任务调用上限', async () => {
    const harness = createHarness(async path => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: false});
        throw new Error(`未预期的本地请求：${path}`);
    });

    await harness.init();

    assert.match(harness.nodes.get('agentTaskStatus').textContent, /模型配置或单任务调用上限未就绪/);
});

test('配置档案只从服务端 profiles 选择并绑定 config_snapshot_id', async () => {
    const createPayloads = [];
    const harness = createHarness(async (path, options) => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: true, profiles: [
            {config_snapshot_id: 'profile-a', provider_id: 'provider-a', model: 'model-a', mode: 'approval'},
            {config_snapshot_id: 'profile-b', provider_id: 'provider-b', model: 'model-b', mode: 'automatic'}
        ]});
        if (path === '/api/agent/runs' && options.method === 'POST') {
            createPayloads.push(JSON.parse(options.body));
            return jsonResponse({job_id: 'job_agent_run-profile-test', run_id: 'run-profile-test', version: 1});
        }
        if (path === '/api/agent/runs/run-profile-test') return jsonResponse({version: 1, status: 'succeeded'});
        if (path.startsWith('/api/agent/runs/run-profile-test/events?')) return jsonResponse({events: [], cursor: null});
        if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
        if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
        if (path.endsWith('/reviews')) return jsonResponse({reviews: []});
        throw new Error(`未预期的本地请求：${path}`);
    });

    await harness.init();
    const profileSelect = harness.nodes.get('agentTaskConfigProfile');
    assert.match(profileSelect.innerHTML, /profile-a/);
    assert.match(profileSelect.innerHTML, /profile-b/);
    profileSelect.value = 'profile-b';
    await harness.create();

    assert.equal(createPayloads.length, 1);
    assert.equal(createPayloads[0].config_snapshot_id, 'profile-b');
    assert.equal(createPayloads[0].provider_id, 'provider-b');
    assert.equal(createPayloads[0].model, 'model-b');
    assert.equal(createPayloads[0].mode, 'automatic');
    assert.deepEqual(createPayloads[0].input_artifact_refs, []);
});

test('暂停任务可用版本 CAS 将所选服务端配置应用到 configuration 接口', async () => {
    const configurationPosts = [];
    const harness = createHarness(async (path, options) => {
        if (path === '/api/asset-registry/projects?archived=false&deleted=false') return jsonResponse({projects: []});
        if (path === '/api/agent/config') return jsonResponse({ready: true, profiles: [
            {config_snapshot_id: 'profile-paused-a', provider_id: 'provider-a', model: 'model-a', mode: 'approval'},
            {config_snapshot_id: 'profile-paused-b', provider_id: 'provider-b', model: 'model-b', mode: 'automatic'}
        ]});
        if (path === '/api/agent/runs/paused-run' && options.method !== 'POST') return jsonResponse({version: 7, status: 'paused'});
        if (path === '/api/agent/runs/paused-run/configuration' && options.method === 'POST') {
            configurationPosts.push(JSON.parse(options.body));
            return jsonResponse({version: 8, status: 'paused'});
        }
        if (path.startsWith('/api/agent/runs/paused-run/events?')) return jsonResponse({events: [], cursor: null});
        if (path.endsWith('/candidate')) return jsonResponse({candidate: null});
        if (path.endsWith('/clarifications')) return jsonResponse({questions: [], is_pending: false});
        if (path.endsWith('/reviews')) return jsonResponse({reviews: []});
        throw new Error(`未预期的本地请求：${path}`);
    });

    await harness.init();
    await harness.load('job_agent_paused-run');
    const applyButton = harness.nodes.get('agentTaskApplyConfiguration');
    assert.equal(applyButton.disabled, false);
    harness.nodes.get('agentTaskConfigProfile').value = 'profile-paused-b';
    await applyButton.emit('click');

    assert.equal(configurationPosts.length, 1);
    assert.equal(configurationPosts[0].config_snapshot_id, 'profile-paused-b');
    assert.equal(configurationPosts[0].expected_version, 7);
    assert.ok(configurationPosts[0].idempotency_key);
    assert.ok(configurationPosts[0].request_id);
    assert.equal(harness.calls.some(call => call.path === '/api/agent/runs/paused-run/configuration'), true);
});
