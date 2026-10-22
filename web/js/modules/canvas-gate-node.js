/**
 * Copyright 2026 Gods-Workbench Authors
 * Licensed under the Apache License, Version 2.0
 *
 * canvas-gate-node.js — God-Canvas System 1 Gate 节点渲染与分支联动契约。
 */
(function(global) {
  'use strict';

  const GATE_NODE_KIND = 'gate';
  const GATE_IN_PORT_ID = 'in';
  const CONDITION_COLOR = '#a5f3fc';

  const OPERATORS = {
    '>=': (score, threshold) => score >= threshold,
    '<=': (score, threshold) => score <= threshold,
    '==': (score, threshold) => score === threshold
  };

  function isGate(node) {
    return Boolean(node) && node.kind === GATE_NODE_KIND;
  }

  function normalizeOptions(value) {
    if (!Array.isArray(value)) return [];
    return value.map(item => String(item ?? '').trim()).filter(Boolean);
  }

  function normalizeCondition(value) {
    const source = value && typeof value === 'object' ? value : {};
    const decisionType = ['boolean', 'choice', 'scoring'].includes(source.decision_type)
      ? source.decision_type
      : 'boolean';
    const condition = {
      decision_type: decisionType,
      options: normalizeOptions(source.options),
      operator: Object.hasOwn(OPERATORS, source.operator) ? source.operator : '>=',
      threshold: Number.isFinite(Number(source.threshold)) ? Number(source.threshold) : 70
    };
    if (source.score_id != null) condition.score_id = String(source.score_id).trim();
    return condition;
  }

  function gatePorts(condition) {
    const normalized = normalizeCondition(condition);
    const branches = normalized.decision_type === 'boolean'
      ? ['true', 'false']
      : normalized.options;
    return [
      {port_id: GATE_IN_PORT_ID, data_type: 'CONDITION'},
      ...branches.map(port_id => ({port_id, data_type: 'CONDITION'}))
    ];
  }

  function gateMetadata(node) {
    const condition = normalizeCondition(node?.condition);
    return {
      kind: GATE_NODE_KIND,
      title: node?.title || 'Gate / Semantic Branch',
      in_ports: [{port_id: GATE_IN_PORT_ID, name: 'System 1', data_type: 'CONDITION'}],
      out_ports: gatePorts(condition).filter(port => port.port_id !== GATE_IN_PORT_ID)
        .map(port => ({port_id: port.port_id, name: port.port_id, data_type: 'CONDITION'})),
      condition
    };
  }

  function connectionFromPort(connection) {
    return connection?.from_port || connection?.from_port_id || '';
  }

  function connectionTarget(connection) {
    return connection?.to_node || connection?.to || '';
  }

  function normalizeNodeMap(nodes) {
    return new Map((Array.isArray(nodes) ? nodes : []).map(node => [node.entity_id, node]));
  }

  function collectTriggered(gate, branch, connections, nodes) {
    const nodeIds = normalizeNodeMap(nodes);
    const triggered = new Set();
    const edges = new Set();
    const queue = [{source: gate.entity_id, port: branch}];
    while (queue.length) {
      const current = queue.shift();
      (Array.isArray(connections) ? connections : []).forEach(connection => {
        const source = connection?.from_node || connection?.from;
        if (source !== current.source) return;
        const port = connectionFromPort(connection);
        if (current.port && port !== current.port) return;
        const target = connectionTarget(connection);
        const edge = `${connection.connection_id}:${target}`;
        if (edges.has(edge)) return;
        edges.add(edge);
        if (nodeIds.has(target)) triggered.add(target);
        queue.push({source: target, port: ''});
      });
    }
    return [...triggered];
  }

  function resolveBranch(condition, result = {}) {
    if (condition.decision_type === 'boolean') {
      if (typeof result.value !== 'boolean') throw new Error('boolean 决策必须返回布尔 value');
      return result.value ? 'true' : 'false';
    }
    if (condition.decision_type === 'choice') {
      const branch = String(result.choice_id ?? '').trim();
      if (!branch) throw new Error('choice 决策必须返回非空 choice_id');
      if (condition.options.length && !condition.options.includes(branch)) {
        throw new Error('choice 决策不在 Gate condition options 中');
      }
      return branch;
    }
    const scores = result.scores && typeof result.scores === 'object' ? result.scores : {};
    const entries = Object.entries(scores);
    if (!entries.length) throw new Error('scoring 决策必须返回 scores');
    const scoreId = condition.score_id || (entries.length === 1 ? entries[0][0] : '');
    if (!scoreId || !Object.hasOwn(scores, scoreId)) {
      throw new Error('scoring 决策缺少 condition.score_id');
    }
    const score = Number(scores[scoreId]);
    if (!Number.isFinite(score)) throw new Error('scoring 分数必须是有限数字');
    return OPERATORS[condition.operator](score, condition.threshold) ? 'true' : 'false';
  }

  function evaluate(node, decision, connections, nodes) {
    if (!isGate(node)) throw new Error('仅 kind=gate 节点可执行 Gate 求值');
    const decisionType = decision?.decision_type || decision?.type;
    const condition = normalizeCondition(node.condition);
    if (decisionType !== condition.decision_type) {
      throw new Error('decision_type 与 Gate condition 不一致');
    }
    const branch = resolveBranch(condition, decision.result);
    return {
      branch,
      triggered_node_ids: collectTriggered(node, branch, connections, nodes),
      decision,
      condition
    };
  }

  function decorateStage(stage, nodes) {
    if (!stage || stage.nodeType !== 1) return;
    stage.querySelectorAll('[data-gw-gate-branch]').forEach(element => element.remove());
    (Array.isArray(nodes) ? nodes : []).forEach(node => {
      if (!isGate(node)) return;
      const overlay = document.createElement('div');
      overlay.className = 'gw-canvas-gate-node-badge';
      overlay.dataset.gwGateBranch = node.entity_id;
      overlay.style.cssText = [
        'position:absolute',
        'right:10px',
        'top:10px',
        'z-index:30',
        'padding:4px 8px',
        'border:1px solid rgba(165,243,252,.45)',
        'border-radius:999px',
        'background:rgba(8,10,14,.88)',
        'color:#a5f3fc',
        'font:700 11px/1 var(--gw-font-mono, monospace)',
        'letter-spacing:.08em',
        'pointer-events:none'
      ].join(';');
      overlay.textContent = `SYSTEM 1 · ${normalizeCondition(node.condition).decision_type.toUpperCase()}`;
      stage.appendChild(overlay);
    });
  }

  global.GodCanvasGateNode = Object.freeze({
    CONDITION_COLOR,
    GATE_IN_PORT_ID,
    GATE_NODE_KIND,
    collectTriggered,
    decorateStage,
    evaluate,
    gateMetadata,
    gatePorts,
    isGate,
    normalizeCondition
  });
})(window);
