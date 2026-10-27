import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../../web/js/modules/agent-gate-card.js', import.meta.url), 'utf8');

function createMockDOM() {
  function matchesSelector(el, selector) {
    if (!el || typeof el !== 'object') return false;
    if (selector.startsWith('[data-action="') && selector.endsWith('"]')) {
      return el.dataset?.action === selector.slice(14, -2);
    }
    if (selector.startsWith('[data-field="') && selector.endsWith('"]')) {
      return el.dataset?.field === selector.slice(13, -2);
    }
    if (selector.startsWith('[data-role="') && selector.endsWith('"]')) {
      return el.dataset?.role === selector.slice(12, -2);
    }
    if (selector.startsWith('.')) {
      return el.className?.includes(selector.slice(1));
    }
    return false;
  }

  function makeElement(tagName) {
    const listeners = new Map();
    const children = [];
    const attributes = new Map();
    const dataset = {};

    const el = {
      tagName: tagName.toUpperCase(),
      children,
      dataset,
      disabled: false,
      value: '',
      rows: 1,
      maxLength: -1,
      type: 'button',
      className: '',
      parentElement: null,

      _textContent: '',
      get textContent() {
        if (children.length > 0) {
          return children.map(c => typeof c === 'string' ? c : c.textContent).join('');
        }
        return this._textContent;
      },
      set textContent(val) {
        children.length = 0;
        this._textContent = String(val ?? '');
      },

      addEventListener(type, handler) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(handler);
      },

      async emit(type, event = {}) {
        const handlers = listeners.get(type) || [];
        for (const handler of handlers) {
          await handler({ ...event, target: this, preventDefault: () => {} });
        }
      },

      appendChild(child) {
        if (typeof child === 'string') {
          children.push(child);
        } else {
          child.parentElement = this;
          children.push(child);
        }
        return child;
      },

      replaceChildren(...newChildren) {
        children.length = 0;
        for (const child of newChildren) {
          this.appendChild(child);
        }
      },

      querySelector(selector) {
        for (const child of children) {
          if (typeof child === 'string') continue;
          if (matchesSelector(child, selector)) return child;
          const match = child.querySelector?.(selector);
          if (match) return match;
        }
        return null;
      },

      querySelectorAll(selector) {
        const results = [];
        for (const child of children) {
          if (typeof child === 'string') continue;
          if (matchesSelector(child, selector)) results.push(child);
          if (child.querySelectorAll) {
            results.push(...child.querySelectorAll(selector));
          }
        }
        return results;
      },

      setAttribute(name, val) { attributes.set(name, String(val)); },
      getAttribute(name) { return attributes.get(name) ?? null; }
    };

    return el;
  }

  const document = {
    createElement(tagName) {
      return makeElement(tagName);
    }
  };

  return { document };
}

function loadModule() {
  const dom = createMockDOM();
  const context = {
    window: { document: dom.document },
    document: dom.document,
    globalThis: {},
    console
  };
  context.globalThis.window = context.window;

  runInNewContext(source, context);
  const GodAgentGateCard = context.window.GodAgentGateCard || context.globalThis.GodAgentGateCard;
  return { GodAgentGateCard, document: dom.document };
}

test('AC1 Reference Card: rendering, selecting, rejecting, notes, order', async () => {
  const { GodAgentGateCard } = loadModule();

  let selectedAsset = null;
  let rejectedAsset = null;
  let updatedNote = null;
  let updatedOrder = null;

  const card = GodAgentGateCard.createReferenceCard({
    asset_id: 'asset-001',
    score: 95,
    tags: ['主角', '高清'],
    summary: '极佳近景镜头',
    status: 'pending',
    note: '初始备注',
    order: 1
  }, {
    onSelect: (data) => { selectedAsset = data.asset_id; },
    onReject: (data) => { rejectedAsset = data.asset_id; },
    onNoteChange: (data, note) => { updatedNote = note; },
    onOrderChange: (data, order) => { updatedOrder = order; }
  });

  assert.equal(card.dataset.assetId, 'asset-001');
  assert.equal(card.dataset.status, 'pending');
  assert.match(card.textContent, /asset-001/);
  assert.match(card.textContent, /95/);
  assert.match(card.textContent, /主角/);

  // Select
  const selectBtn = card.querySelector('[data-action="select"]');
  assert.ok(selectBtn, 'select button should be found');
  await selectBtn.emit('click');
  assert.equal(card.dataset.status, 'selected');
  assert.equal(selectedAsset, 'asset-001');

  // Reject
  const rejectBtn = card.querySelector('[data-action="reject"]');
  assert.ok(rejectBtn, 'reject button should be found');
  await rejectBtn.emit('click');
  assert.equal(card.dataset.status, 'rejected');
  assert.equal(rejectedAsset, 'asset-001');

  // Note change with limit check <= 500
  const noteArea = card.querySelector('[data-field="note"]');
  assert.ok(noteArea, 'note textarea should be found');
  assert.equal(noteArea.value, '初始备注');

  noteArea.value = '更新的新备注内容';
  await noteArea.emit('input');
  assert.equal(updatedNote, '更新的新备注内容');

  // Note truncation test (> 500 chars)
  const longNote = 'A'.repeat(600);
  noteArea.value = longNote;
  await noteArea.emit('input');
  assert.equal(noteArea.value.length, 500);
  assert.equal(updatedNote.length, 500);

  // Order adjustments
  const upBtn = card.querySelector('[data-action="move-up"]');
  assert.ok(upBtn, 'move-up button should be found');
  await upBtn.emit('click');
  assert.equal(updatedOrder, 0);

  const downBtn = card.querySelector('[data-action="move-down"]');
  assert.ok(downBtn, 'move-down button should be found');
  await downBtn.emit('click');
  assert.equal(updatedOrder, 1);
});

test('AC1 Gate Card: approve, changes_requested, rolled_back and state transition', async () => {
  const { GodAgentGateCard } = loadModule();

  let submittedDecision = null;

  const gateCard = GodAgentGateCard.createGateCard({
    gate_id: 'gate-101',
    gate_kind: 'ScriptApproval',
    summary: '剧本第一期审查',
    status: 'pending'
  }, {
    onSubmitDecision: async (gate, decision) => {
      submittedDecision = decision;
    }
  });

  assert.equal(gateCard.dataset.gateId, 'gate-101');
  assert.equal(gateCard.dataset.status, 'pending');
  assert.match(gateCard.textContent, /ScriptApproval/);

  const approveBtn = gateCard.querySelector('[data-action="approve"]');
  assert.ok(approveBtn, 'approve button should be found');
  await approveBtn.emit('click');

  assert.equal(submittedDecision, 'approve');
  assert.equal(gateCard.dataset.status, 'finished');
  assert.match(gateCard.textContent, /已确认同意/);

  // Re-create for changes_requested
  const gateCard2 = GodAgentGateCard.createGateCard({
    gate_id: 'gate-102',
    gate_kind: 'StoryboardCheck',
    summary: '分镜检查',
    status: 'pending'
  }, {
    onSubmitDecision: async (gate, decision) => {
      submittedDecision = decision;
    }
  });

  const changesBtn = gateCard2.querySelector('[data-action="changes_requested"]');
  assert.ok(changesBtn, 'changes_requested button should be found');
  await changesBtn.emit('click');
  assert.equal(submittedDecision, 'changes_requested');
  assert.equal(gateCard2.dataset.status, 'finished');
  assert.match(gateCard2.textContent, /已要求修改/);

  // Re-create for rolled_back
  const gateCard3 = GodAgentGateCard.createGateCard({
    gate_id: 'gate-103',
    gate_kind: 'MasterGate',
    summary: '母带门禁',
    status: 'pending'
  }, {
    onSubmitDecision: async (gate, decision) => {
      submittedDecision = decision;
    }
  });

  const rollbackBtn = gateCard3.querySelector('[data-action="rolled_back"]');
  assert.ok(rollbackBtn, 'rolled_back button should be found');
  await rollbackBtn.emit('click');
  assert.equal(submittedDecision, 'rolled_back');
  assert.equal(gateCard3.dataset.status, 'finished');
  assert.match(gateCard3.textContent, /已退回/);
});

test('AC1 & Recovery: session state recovery restores identical card state', () => {
  const { GodAgentGateCard, document } = loadModule();

  const mockSession = {
    references: [
      { asset_id: 'asset-88', score: 90, tags: ['角1'], summary: '候选主角', status: 'selected', note: '确认保留', order: 2 }
    ],
    gates: [
      { gate_id: 'gate-88', gate_kind: 'RenderGate', summary: '渲染质量确认', status: 'finished', decision: 'approve' }
    ],
    progress: {
      current_step: '生成样片中',
      total_steps: '3/5',
      status: 'running',
      failure_reason: ''
    }
  };

  const container = document.createElement('div');
  GodAgentGateCard.createAgentPanel(container, mockSession);

  assert.equal(container.children.length, 5); // Progress panel, Gate header, Gate card, Reference header, Reference card

  const restoredState = GodAgentGateCard.restoreSessionState(mockSession);
  assert.equal(restoredState.references[0].status, 'selected');
  assert.equal(restoredState.references[0].note, '确认保留');
  assert.equal(restoredState.gates[0].decision, 'approve');
  assert.equal(restoredState.progress.current_step, '生成样片中');
});

test('AC3 Plain text rendering prevents XSS vulnerabilities', () => {
  const { GodAgentGateCard, document } = loadModule();

  const xssPayload = '<script>alert("xss")</script><img src=x onerror=alert(1)>';

  // Test XSS in Reference Card
  const refCard = GodAgentGateCard.createReferenceCard({
    asset_id: xssPayload,
    summary: xssPayload,
    note: xssPayload
  });
  const noteField = refCard.querySelector('[data-field="note"]');
  assert.ok(noteField, 'note field should exist');
  assert.equal(noteField.value, xssPayload);
  assert.match(refCard.textContent, /<script>alert\("xss"\)<\/script>/);

  // Test XSS in Gate Card
  const gateCard = GodAgentGateCard.createGateCard({
    gate_id: 'xss-gate',
    gate_kind: xssPayload,
    summary: xssPayload
  });
  assert.match(gateCard.textContent, /<script>alert\("xss"\)<\/script>/);

  // Test XSS in Progress Panel Failure Reason
  const progressPanel = GodAgentGateCard.createProgressPanel({
    current_step: xssPayload,
    status: 'failed',
    failure_reason: xssPayload
  });
  const failBox = progressPanel.querySelector('[data-role="failure-reason"]');
  assert.ok(failBox, 'failure-reason box should exist');
  assert.equal(failBox.textContent, `失败原因:${xssPayload}`);

  // Test XSS in Error Render
  const errContainer = document.createElement('div');
  GodAgentGateCard.renderError(errContainer, xssPayload);
  const errBox = errContainer.querySelector('[data-role="error-message"]');
  assert.ok(errBox, 'error-message box should exist');
  assert.equal(errBox.textContent, `操作失败${xssPayload}`);
});

test('AC4 Mock API failure shows clear error state without white screen / crash', async () => {
  const { GodAgentGateCard, document } = loadModule();

  let errorCaught = null;

  const gateCard = GodAgentGateCard.createGateCard({
    gate_id: 'gate-500',
    gate_kind: 'FailGate',
    summary: '会触发 500 错的门禁',
    status: 'pending'
  }, {
    onSubmitDecision: async () => {
      throw new Error('HTTP 500 Service Unavailable');
    },
    onError: (err) => {
      errorCaught = err;
    }
  });

  const approveBtn = gateCard.querySelector('[data-action="approve"]');
  assert.ok(approveBtn, 'approve button should exist');
  await approveBtn.emit('click');

  assert.ok(errorCaught);
  assert.equal(errorCaught.message, 'HTTP 500 Service Unavailable');
  assert.equal(gateCard.dataset.status, 'pending');

  // Test panel error rendering for failed fetch
  const container = document.createElement('div');
  GodAgentGateCard.createAgentPanel(container, { error: '网络请求超时 (504 Gateway Timeout)' });

  const errorBox = container.querySelector('[data-role="error-message"]');
  assert.ok(errorBox, 'error box should exist');
  assert.match(errorBox.textContent, /504 Gateway Timeout/);
});
