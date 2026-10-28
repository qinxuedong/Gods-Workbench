// MVP2-T7b（issue #416）生产链门禁确认卡 node 测试：
// AC1 覆盖确认/要求修改/退回、提交后等待态与完成态；
// AC2 纯原生实现，样式使用现有黑金 token / CSS 变量；
// AC3 用户输入与服务端错误信息一律纯文本渲染（防 XSS）；
// AC4 mock API 失败时给出明确错误态，不白屏。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
    GATE_DECISIONS,
    GATE_DECISION_LABELS,
    GATE_KINDS,
    GATE_KIND_LABELS,
    GATE_PHASES,
    GATE_REASON_MAX_LENGTH,
    GATE_STATES,
    GATE_STATE_LABELS,
    GATE_UNKNOWN_TEXT,
    gateIsDecidable,
    kindLabelOf,
    loadGates,
    normalizeGate,
    normalizeGateKind,
    normalizeGates,
    normalizeGateState,
    renderGateCard,
    renderGateCards,
    resolveGateDecisionClick,
    resolveGateReason,
    stateLabelOf,
    submitGateDecision,
} from '../../web/js/modules/agent-gate-card.js';

const repoRoot = process.cwd();
const read = relativePath => readFileSync(path.join(repoRoot, relativePath), 'utf8');

const PENDING_GATE = {
    gate_id: 'gate-1',
    run_id: 'run-1',
    kind: 'storyboard',
    state: 'pending',
    version: 2,
    summary: '分镜共 12 个镜头，等待确认后进入样片。',
};

// 全部网络调用走注入的 mock，测试不接触真实后端。
function mockFetch(handler) {
    const calls = [];
    const runner = async (url, options) => {
        calls.push({ url, options });
        return handler(url, options);
    };
    return { calls, runner };
}

function jsonResponse(value, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: () => 'application/json' },
        json: async () => value,
    };
}

function decisionButtons(html) {
    return [...html.matchAll(/<button[^>]*data-gate-decision="([^"]+)"([^>]*)>/g)]
        .map(match => ({ decision: match[1], attrs: match[2] }));
}

test('AC1 待确认门禁展示类别、摘要与三个决策按钮', () => {
    const html = renderGateCard(PENDING_GATE, { phase: 'idle' });
    assert.ok(html.includes('data-gate-kind="storyboard"'), '应标记门禁类别');
    assert.ok(html.includes('data-gate-state="pending"'), '应标记门禁状态');
    assert.ok(html.includes('data-gate-version="2"'), '应带出版本供乐观锁使用');
    assert.ok(html.includes('分镜门禁'), `应显示类别中文名：${html}`);
    assert.ok(html.includes('等待确认'), '应显示状态中文名');
    assert.ok(html.includes(PENDING_GATE.summary), '应显示门禁摘要');
    const buttons = decisionButtons(html);
    assert.deepEqual(buttons.map(button => button.decision), [...GATE_DECISIONS]);
    for (const decision of GATE_DECISIONS) {
        assert.ok(html.includes(GATE_DECISION_LABELS[decision]), `应显示决策按钮文案 ${decision}`);
    }
    assert.ok(html.includes('data-gate-reason="gate-1"'), '应提供理由输入框');
});

test('AC1 提交后进入等待态：按钮全部禁用且提示正在提交', () => {
    for (const decision of GATE_DECISIONS) {
        const html = renderGateCard(PENDING_GATE, { phase: 'submitting', pendingDecision: decision });
        assert.ok(html.includes('data-gate-phase="submitting"'), '应标记等待相位');
        assert.ok(html.includes(`正在提交「${GATE_DECISION_LABELS[decision]}」…`), `应提示正在提交 ${decision}`);
        for (const button of decisionButtons(html)) {
            assert.ok(button.attrs.includes('disabled'), `${button.decision} 在等待期间应禁用`);
        }
    }
    const waiting = renderGateCard(PENDING_GATE, { phase: 'submitting', pendingDecision: 'approve' });
    assert.ok(waiting.includes('确认通过中…'), '等待中的按钮应给出进行中文案');
});

test('AC1 三种决策生效后均渲染为完成态且不再提供按钮', () => {
    for (const state of ['approved', 'changes_requested', 'rolled_back']) {
        const html = renderGateCard({ ...PENDING_GATE, state }, { phase: 'idle' });
        assert.ok(html.includes(`data-gate-state="${state}"`), `应标记 ${state}`);
        assert.ok(html.includes(GATE_STATE_LABELS[state]), `应显示 ${state} 中文名`);
        assert.ok(html.includes(`门禁已处理：${GATE_STATE_LABELS[state]}`), `${state} 应给出完成文案`);
        assert.equal(decisionButtons(html).length, 0, `${state} 为已处理门禁，不应再有决策按钮`);
        assert.equal(html.includes('data-gate-reason'), false, '已处理门禁不再收集理由');
    }
});

test('AC1 三种决策都按稳定 URL 与负载提交，并回写服务端门禁状态', async () => {
    for (const decision of GATE_DECISIONS) {
        const { calls, runner } = mockFetch(async () => jsonResponse({
            gate: { gate_id: 'gate-1', run_id: 'run-1', kind: 'storyboard', state: 'approved', version: 3 },
        }));
        const updated = await submitGateDecision({
            runId: 'run-1',
            gateId: 'gate-1',
            decision,
            reason: '分镜节奏已核验',
            expectedVersion: 2,
            idempotencyKey: 'idem-1',
            requestId: 'req-1',
            fetchImpl: runner,
        });
        assert.equal(calls.length, 1, `${decision} 应只发一次请求`);
        assert.equal(calls[0].url, '/api/agent/runs/run-1/gates/gate-1/decision');
        assert.equal(calls[0].options.method, 'POST');
        assert.equal(calls[0].options.credentials, 'same-origin');
        const body = JSON.parse(calls[0].options.body);
        assert.equal(body.decision, decision);
        assert.equal(body.reason, '分镜节奏已核验');
        assert.equal(body.expected_version, 2, '应携带 expected_version 供乐观锁 CAS');
        assert.equal(body.idempotency_key, 'idem-1');
        assert.equal(body.request_id, 'req-1');
        assert.equal(updated.gate_id, 'gate-1');
        assert.equal(updated.state, 'approved');
        assert.equal(updated.version, 3);
    }
});

test('AC1 未知决策、缺失编号与非法版本一律拒绝提交', async () => {
    const { runner } = mockFetch(async () => jsonResponse({ gate: PENDING_GATE }));
    for (const decision of ['approve_with_money', '', null, undefined, 'APPROVE']) {
        await assert.rejects(
            () => submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision, reason: '理由', expectedVersion: 0, fetchImpl: runner }),
            /不支持的门禁决策/,
            `决策 ${String(decision)} 应被拒绝`
        );
    }
    await assert.rejects(
        () => submitGateDecision({ runId: '', gateId: 'gate-1', decision: 'approve', expectedVersion: 0, fetchImpl: runner }),
        /缺少任务编号/
    );
    await assert.rejects(
        () => submitGateDecision({ runId: 'run-1', gateId: '', decision: 'approve', expectedVersion: 0, fetchImpl: runner }),
        /缺少门禁编号/
    );
    for (const version of [-1, 1.5, '2', null, undefined]) {
        await assert.rejects(
            () => submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision: 'approve', reason: '理由', expectedVersion: version, fetchImpl: runner }),
            /门禁版本缺失/,
            `版本 ${String(version)} 应被拒绝`
        );
    }
});

test('AC1 理由缺省：确认通过回填固定文案，要求修改与退回必须显式填写', () => {
    assert.equal(resolveGateReason('', 'approve'), GATE_DECISION_LABELS.approve);
    assert.equal(resolveGateReason('   ', 'approve'), GATE_DECISION_LABELS.approve);
    assert.equal(resolveGateReason('需重排第三场', 'changes_requested'), '需重排第三场');
    assert.equal(resolveGateReason('  ', 'changes_requested'), null);
    assert.equal(resolveGateReason('', 'rolled_back'), null);
    assert.equal(resolveGateReason(null, 'rolled_back'), null);
    assert.equal(resolveGateReason(undefined, 'rolled_back'), null);
    assert.equal(resolveGateReason('x'.repeat(GATE_REASON_MAX_LENGTH + 100), 'approve').length, GATE_REASON_MAX_LENGTH);
});

test('AC1 事件委托解析按钮命中，未命中返回 null', () => {
    const event = {
        target: {
            closest: selector => (selector === '[data-gate-decision]'
                ? { getAttribute: name => (name === 'data-gate-decision' ? 'rolled_back' : null), closest: () => ({ getAttribute: () => 'gate-7' }) }
                : null),
        },
    };
    assert.deepEqual(resolveGateDecisionClick(event), { gateId: 'gate-7', decision: 'rolled_back' });
    assert.equal(resolveGateDecisionClick({ target: { closest: () => null } }), null);
    assert.equal(resolveGateDecisionClick({}), null);
    assert.equal(resolveGateDecisionClick(null), null);
});

test('AC2 枚举与后端 GateKind / GateState 对齐，标签完备', () => {
    assert.deepEqual([...GATE_KINDS], ['script', 'storyboard', 'animatic', 'i2v_submit', 'master_output']);
    assert.deepEqual([...GATE_STATES], ['pending', 'approved', 'changes_requested', 'rolled_back']);
    assert.deepEqual([...GATE_DECISIONS], ['approve', 'changes_requested', 'rolled_back']);
    for (const kind of GATE_KINDS) {
        assert.equal(normalizeGateKind(kind), kind);
        assert.ok(GATE_KIND_LABELS[kind], `${kind} 缺中文名`);
    }
    for (const state of GATE_STATES) {
        assert.equal(normalizeGateState(state), state);
        assert.ok(GATE_STATE_LABELS[state], `${state} 缺中文名`);
    }
    assert.deepEqual(Object.values(GATE_PHASES), ['idle', 'submitting', 'error']);
    assert.equal(GATE_PHASES.idle, 'idle');
    assert.equal(GATE_PHASES.submitting, 'submitting');
    assert.equal(GATE_PHASES.error, 'error');
});

test('AC2 只有 pending 可操作，未知状态按不可操作处理（fail closed）', () => {
    assert.equal(gateIsDecidable(PENDING_GATE), true);
    for (const state of ['approved', 'changes_requested', 'rolled_back']) {
        assert.equal(gateIsDecidable({ ...PENDING_GATE, state }), false, `${state} 不应可操作`);
    }
    assert.equal(gateIsDecidable({ ...PENDING_GATE, state: 'garbage' }), false);
    assert.equal(gateIsDecidable(null), false);
    assert.equal(gateIsDecidable({ run_id: 'run-1' }), false);
});

test('AC2 每条门禁都可渲染为独立卡片，空列表与脏数据不占位', () => {
    const html = renderGateCards({
        gates: [
            PENDING_GATE,
            { gate_id: 'gate-2', kind: 'master_output', state: 'approved', version: 1, summary: '母带已输出' },
            { gate_id: 'gate-3', kind: 'i2v_submit', state: 'pending', version: 0, summary: '图生视频提交' },
        ],
    }, {});
    assert.equal((html.match(/<section class="gw-gate-card"/g) || []).length, 3);
    for (const kind of ['storyboard', 'master_output', 'i2v_submit']) {
        assert.ok(html.includes(`data-gate-kind="${kind}"`), `应渲染 ${kind} 卡片`);
    }
    assert.equal(renderGateCards([], {}), '');
    assert.equal(renderGateCards({ gates: [] }, {}), '');
    assert.equal(renderGateCards(null, {}), '');
    assert.equal(renderGateCards(undefined, {}), '');
    assert.equal(renderGateCards('门禁', {}), '');
    assert.equal(renderGateCards(42, {}), '');
    for (const bad of [null, undefined, {}, [], 'gate', 7, { gate_id: '' }, { gate_id: null }, { gate_id: {} }, { run_id: 'run-1', kind: 'script' }]) {
        assert.equal(renderGateCard(bad, {}), '', `非法门禁 ${JSON.stringify(bad)} 不应渲染`);
    }
});

test('AC2 视图按 gate_id 取值，支持 Map 与普通对象，非法相位回退 idle', () => {
    const views = new Map([['gate-1', { phase: 'submitting', pendingDecision: 'approve', reason: '保留理由' }]]);
    const html = renderGateCards([PENDING_GATE], views);
    assert.ok(html.includes('data-gate-phase="submitting"'));
    assert.ok(html.includes('保留理由'));
    assert.ok(renderGateCards([PENDING_GATE], { 'gate-1': { phase: 'submitting', pendingDecision: 'rolled_back' } }).includes('正在提交「退回」…'));
    for (const bad of [null, undefined, 'phase', { phase: 'garbage' }]) {
        assert.ok(renderGateCard(PENDING_GATE, bad).includes('data-gate-phase="idle"'), '非法相位应回退 idle');
    }
});

test('AC2 未知类别与状态显示「未知」且不猜测', () => {
    const html = renderGateCard({ gate_id: 'gate-1', kind: 'unknown_kind', state: 'unknown_state', version: 0 }, {});
    assert.ok(html.includes(`${GATE_UNKNOWN_TEXT}门禁`), '未知类别应显示未知');
    assert.ok(html.includes(GATE_UNKNOWN_TEXT), '未知状态应显示未知');
    assert.equal(decisionButtons(html).length, 0, '未知状态不得提供按钮');
    assert.equal(kindLabelOf('nope'), GATE_UNKNOWN_TEXT);
    assert.equal(stateLabelOf('nope'), GATE_UNKNOWN_TEXT);
    assert.equal(normalizeGateKind('nope'), null);
    assert.equal(normalizeGateState('nope'), null);
    const missing = renderGateCard({ gate_id: 'gate-1', kind: 'script', state: 'pending', version: 0 }, {});
    assert.ok(missing.includes('剧本阶段摘要不可用'), '摘要缺失应给出占位文案');
});

test('AC3 摘要、理由与错误信息全部转义，不产生可跳转链接', () => {
    const payload = '<script>alert(1)</script>';
    const html = renderGateCard(
        { gate_id: 'gate-1', kind: 'script', state: 'pending', version: 0, summary: payload },
        { phase: 'error', message: payload, reason: payload }
    );
    assert.ok(!html.includes('<script'), '不得输出原始 script 标签');
    assert.ok(html.includes('&lt;script&gt;'), 'script 应转义为纯文本');
    assert.ok(!/href=/i.test(html), '门禁卡不得输出 href 属性');
    assert.ok(html.includes('提交失败：&lt;script&gt;alert(1)&lt;/script&gt;'), '错误信息应按纯文本渲染');

    const breakout = '"><img src=x onerror=alert(2)>';
    const escaped = renderGateCard(
        { gate_id: breakout, kind: 'script', state: 'pending', version: 0, summary: breakout },
        { phase: 'error', message: breakout, reason: breakout }
    );
    assert.ok(!escaped.includes('<img'), '不得输出原始 img 标签');
    assert.ok(escaped.includes('&lt;img'), 'img 应转义为纯文本');
    assert.ok(escaped.includes('&quot;&gt;&lt;img'), '引号与尖括号应转义');
    // 注入片段必须整体落在转义文本里，不能拼出可解析的属性。
    assert.equal(escaped.includes('data-gate-id=""><img'), false, '门禁编号不得逃逸出属性');
});

test('AC3 宽松容错：数值摘要转文本、脏数据被丢弃、异常输入不抛', () => {
    const normalized = normalizeGate({ gate_id: 'gate-1', kind: 'script', state: 'pending', version: '3', summary: 42 });
    assert.equal(normalized.version, 0, '非整数版本归零，不猜测');
    assert.equal(normalized.summary, '42', '数值摘要转文本');
    assert.equal(normalizeGate({ gate_id: 42, kind: 'script', state: 'pending', version: 0 }).gate_id, '42', '数值编号转文本');
    assert.deepEqual(normalizeGates({ gates: [PENDING_GATE, null, 'gate', 7, { run_id: 'run-1' }] }).map(gate => gate.gate_id), ['gate-1']);
    assert.deepEqual(normalizeGates([PENDING_GATE, { gate_id: 'gate-2', kind: 'animatic', state: 'pending', version: 1 }]).length, 2);
    assert.deepEqual(normalizeGates({}), []);
    assert.deepEqual(normalizeGates([]), []);
    assert.deepEqual(normalizeGates(null), []);
    assert.deepEqual(normalizeGates('gates'), []);
    for (const bad of [null, undefined, {}, [], 'x', 1, { summary: 'y' }]) {
        assert.equal(normalizeGate(bad), null);
    }
});

test('AC4 mock API 失败时抛出带状态的错误，卡片给出明确错误态且保留按钮', async () => {
    const conflict = mockFetch(async () => jsonResponse({ detail: { code: 'REVISION_CONFLICT', message: '门禁版本已变化，请刷新后再决定。' } }, 409));
    await assert.rejects(
        () => submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision: 'approve', reason: '理由', expectedVersion: 0, fetchImpl: conflict.runner }),
        error => error.status === 409 && /门禁版本已变化/.test(error.message),
        '409 应带出服务端错误文案'
    );

    const serverError = mockFetch(async () => jsonResponse({ detail: '门禁服务不可用' }, 500));
    await assert.rejects(
        () => submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision: 'rolled_back', reason: '理由', expectedVersion: 0, fetchImpl: serverError.runner }),
        error => error.status === 500 && error.message === '门禁服务不可用',
        '裸字符串 detail 也应取到'
    );

    const networkDown = mockFetch(async () => { throw new Error('网络不可达'); });
    await assert.rejects(
        () => submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision: 'approve', reason: '理由', expectedVersion: 0, fetchImpl: networkDown.runner }),
        /网络不可达/
    );

    const brokenBody = mockFetch(async () => ({ ok: true, status: 200, json: async () => { throw new Error('bad json'); } }));
    assert.equal(
        await submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision: 'approve', reason: '理由', expectedVersion: 0, fetchImpl: brokenBody.runner }),
        null,
        '响应体不可解析时返回 null，由宿主刷新，不白屏'
    );

    const html = renderGateCard(PENDING_GATE, { phase: 'error', message: '门禁版本已变化，请刷新后再决定。' });
    assert.ok(html.includes('data-gate-phase="error"'), '应标记错误相位');
    assert.ok(html.includes('提交失败：门禁版本已变化，请刷新后再决定。'), '应显示明确错误态');
    for (const decision of GATE_DECISIONS) {
        assert.ok(html.includes(`data-gate-decision="${decision}"`), '错误态应保留按钮供重试');
        for (const button of decisionButtons(html)) {
            assert.equal(button.attrs.includes('disabled'), false, '错误态按钮应可再次提交');
        }
    }
    const emptyMessage = renderGateCard(PENDING_GATE, { phase: 'error', message: '' });
    assert.ok(emptyMessage.includes(`提交失败：${GATE_UNKNOWN_TEXT}`), '错误文案缺失时显示未知，不空白');
});

test('AC4 门禁列表读取失败与降级：错误明确、不抛未捕获异常', async () => {
    const failing = mockFetch(async () => jsonResponse({ detail: { message: '门禁列表读取失败' } }, 503));
    await assert.rejects(() => loadGates({ runId: 'run-1', fetchImpl: failing.runner }), error => error.status === 503 && /门禁列表读取失败/.test(error.message));

    const { calls, runner } = mockFetch(async () => jsonResponse({ gates: [PENDING_GATE, { gate_id: 'gate-2', kind: 'script', state: 'approved', version: 4 }] }));
    const gates = await loadGates({ runId: 'run-1', fetchImpl: runner });
    assert.equal(calls[0].url, '/api/agent/runs/run-1/gates');
    assert.equal(calls[0].options.method, 'GET');
    assert.deepEqual(gates.map(gate => gate.gate_id), ['gate-1', 'gate-2']);
    assert.equal(gates[1].state, 'approved');
    assert.deepEqual(await loadGates({ runId: 'run-1', fetchImpl: (async () => ({ ok: true, status: 200, json: async () => null })).bind(null) }), []);

    for (const bad of [{}, { runId: '' }, { runId: '   ' }, { runId: 42 }]) {
        await assert.rejects(() => loadGates({ ...bad, fetchImpl: runner }), /缺少任务编号/);
    }
    // 显式禁用网络接口时不回退到运行环境自带的 fetch，避免测试误触真实请求。
    await assert.rejects(() => loadGates({ runId: 'run-1', fetchImpl: null }), /没有可用的网络接口/);
    await assert.rejects(
        () => submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision: 'approve', expectedVersion: 0, fetchImpl: null }),
        /没有可用的网络接口/
    );
    await assert.rejects(
        () => submitGateDecision({ runId: 'run-1', gateId: 'gate-1', decision: 'approve', fetchImpl: runner }),
        /门禁版本缺失/
    );
});

test('AC2 模块无外部 URL、无 CommonJS、无框架，样式全部走现有黑金 token', () => {
    // 许可证头注释中的 apache.org 链接不属于运行时资源，剥除后再断言。
    const stripLicenseHeader = source => source.replace(/^\/\*[\s\S]*?\*\//, '');
    const moduleSource = stripLicenseHeader(read('web/js/modules/agent-gate-card.js'));
    assert.ok(!/https?:\/\//.test(moduleSource), '模块不得包含外部 URL');
    assert.ok(!/\brequire\s*\(/.test(moduleSource), '不得使用 CommonJS');
    assert.ok(!/<script\b/i.test(moduleSource), '模块不得内嵌脚本标签');
    for (const token of [
        'var(--gw-surface-card)',
        'var(--gw-border)',
        'var(--gold-light)',
        'var(--gw-warning)',
        'var(--gw-success)',
        'var(--gw-error)',
        'var(--gw-text-muted)',
        'var(--gw-type-body-sm)',
    ]) {
        assert.ok(moduleSource.includes(token), `应使用现有设计 token ${token}`);
    }
    assert.ok(moduleSource.includes('hw-btn hw-btn-sm'), '按钮应复用现有硬件按钮类');
    assert.ok(moduleSource.includes('btn-abort-carmine'), '退回按钮应复用现有危险态类');
});

test('AC2 控制器以仓库内静态路径动态挂载门禁卡模块', () => {
    const controllerSource = read('web/js/controllers/agent-tasks.js');
    assert.ok(controllerSource.includes("const GATE_CARD_MODULE_URL = '/static/js/modules/agent-gate-card.js';"), '应指向仓库内静态模块');
    assert.ok(controllerSource.includes('import(GATE_CARD_MODULE_URL)'), '应以动态 import 挂载');
    assert.ok(controllerSource.includes('renderGateCards(state.gates, state.gateViews)'), '应渲染门禁卡片列表');
    assert.ok(controllerSource.includes('refreshGates(runId, generation, sequence)'), '门禁刷新应并入主刷新批次');
    const moduleUrls = [...controllerSource.matchAll(/const\s+(\w*MODULE_URL)\s*=\s*'([^']+)'/g)].map(match => match[2]);
    assert.deepEqual(moduleUrls, ['/static/js/modules/agent-gate-card.js'], '只允许这一个仓库内模块地址');
    for (const url of moduleUrls) {
        assert.ok(url.startsWith('/static/'), `模块地址必须为仓库内静态资源: ${url}`);
        assert.ok(!/^https?:/.test(url), '不得引入外部 CDN');
    }
    // 控制器自身仍为经典脚本：不得出现静态 import / export，否则页面加载会报错。
    assert.ok(!/^\s*import\s[^'"]*['"][^'"]+['"];?\s*$/m.test(controllerSource), '控制器不得改写为 ES 模块');
    assert.ok(!/^\s*export\s/m.test(controllerSource), '控制器不得导出模块成员');
});

test('测试文件已登记 tests/frontend/node-files.txt（缺失则 CI 红）', () => {
    const listed = read('tests/frontend/node-files.txt');
    assert.ok(listed.includes('tests/frontend/mvp2-t7b-gate-card.test.mjs'), 'node-files.txt 应登记本测试');
});
