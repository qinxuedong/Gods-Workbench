/**
 * Copyright 2026 Gods-Workbench Authors
 * Licensed under the Apache License, Version 2.0
 * 
 * canvas-workspace.test.mjs
 * 验证画布工作台引擎与工作流样式/动效完全对齐
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const WORKSPACE_JS_PATH = path.resolve('web/js/modules/canvas-workspace.js');
const WORKFLOW_CONTROLLER_PATH = path.resolve('web/js/controllers/workflow-controller.js');
const WORKSPACE_CSS_PATH = path.resolve('web/css/pages/canvas-workspace.css');
const WORKFLOW_CSS_PATH = path.resolve('web/css/pages/workflow-workbench.css');

test('画布工作台与工作流 PORT_COLORS 调色板完全对齐', () => {
  const wsJs = fs.readFileSync(WORKSPACE_JS_PATH, 'utf-8');
  const wfJs = fs.readFileSync(WORKFLOW_CONTROLLER_PATH, 'utf-8');

  const expectedColors = {
    MODEL: "#dfc384",
    CLIP: "#38bdf8",
    CONDITIONING: "#38bdf8",
    IMAGE: "#10b981",
    VIDEO: "#dfc384",
    AUDIO: "#38bdf8",
    LATENT: "#c084fc",
    VAE: "#f43f5e"
  };

  for (const [key, color] of Object.entries(expectedColors)) {
    assert.match(wsJs, new RegExp(`${key}:\\s*"${color}"`), `canvas-workspace.js 中缺少或不匹配颜色 ${key}: ${color}`);
    assert.match(wfJs, new RegExp(`${key}:\\s*"${color}"`), `workflow-controller.js 中缺少或不匹配颜色 ${key}: ${color}`);
  }
});

test('画布工作台与工作流 6 级同色同心锁相纺锤流光步长完全对齐', () => {
  const wsJs = fs.readFileSync(WORKSPACE_JS_PATH, 'utf-8');
  const wfJs = fs.readFileSync(WORKFLOW_CONTROLLER_PATH, 'utf-8');

  const expectedSteps = [
    { len: 44, width: 0.45 },
    { len: 35, width: 0.75 },
    { len: 26, width: 1.05 },
    { len: 18, width: 1.35 },
    { len: 11, width: 1.65 },
    { len: 5, width: 1.95 }
  ];

  expectedSteps.forEach(st => {
    assert.ok(wsJs.includes(`len: ${st.len}`), `canvas-workspace 缺少纺锤阶梯 len: ${st.len}`);
    assert.ok(wfJs.includes(`len: ${st.len}`), `workflow-controller 缺少纺锤阶梯 len: ${st.len}`);
  });
});

test('画布工作台 CSS 与工作流 CSS 的流光关键帧动画一致', () => {
  const wsCss = fs.readFileSync(WORKSPACE_CSS_PATH, 'utf-8');
  const wfCss = fs.readFileSync(WORKFLOW_CSS_PATH, 'utf-8');

  assert.ok(wsCss.includes('@keyframes wireCurrentWave'), 'canvas-workspace.css 必须包含 wireCurrentWave 动画');
  assert.ok(wfCss.includes('@keyframes wireCurrentWave'), 'workflow-workbench.css 必须包含 wireCurrentWave 动画');

  assert.ok(wsCss.includes('infinite-grid-bg'), 'canvas-workspace.css 必须包含 infinite-grid-bg');
  assert.ok(wfCss.includes('infinite-grid-bg'), 'workflow-workbench.css 必须包含 infinite-grid-bg');
});

test('画布工作台 DOM 结构包含黑金硬件卡片容器与连线 SVG 契约', () => {
  const htmlPath = path.resolve('web/pages/storyboard.html');
  const html = fs.readFileSync(htmlPath, 'utf-8');

  assert.ok(html.includes('id="canvasWorkspaceStage"'), 'storyboard.html 必须包含 canvasWorkspaceStage');
  assert.ok(html.includes('id="canvasTopologyStage"'), 'storyboard.html 必须包含 canvasTopologyStage');
  assert.ok(html.includes('id="canvasTopologyWiresSvg"'), 'storyboard.html 必须包含 canvasTopologyWiresSvg');
  assert.ok(html.includes('id="canvasTopologyNodesLayer"'), 'storyboard.html 必须包含 canvasTopologyNodesLayer');
});

test('canvas-list 浮窗严格符合 GW-UI-FW-01 与模态规范', () => {
  const canvasListHtml = fs.readFileSync(path.resolve('web/embeds/canvas-list.html'), 'utf-8');
  const canvasListJs = fs.readFileSync(path.resolve('web/js/modules/canvas-list.js'), 'utf-8');

  // HTML 结构契约
  assert.ok(canvasListHtml.includes('id="trashEntry"'), 'canvas-list.html 必须包含 trashEntry 按钮');
  assert.ok(canvasListHtml.includes('id="trashBadge"'), 'canvas-list.html 必须包含 trashBadge 徽标');
  assert.ok(canvasListHtml.includes('id="trashBackdrop"'), 'canvas-list.html 必须包含 trashBackdrop');
  assert.ok(canvasListHtml.includes('id="archiveBackdrop"'), 'canvas-list.html 必须包含 archiveBackdrop');
  assert.ok(canvasListHtml.includes('id="trashPanel"'), 'canvas-list.html 必须包含 trashPanel');
  assert.ok(canvasListHtml.includes('id="archivePanel"'), 'canvas-list.html 必须包含 archivePanel');

  // JS 行为契约 (Focus 还原与 Escape / 外部点击)
  assert.ok(canvasListJs.includes('trashReturnFocusEl'), 'canvas-list.js 必须具备 trashReturnFocusEl 焦点还原');
  assert.ok(canvasListJs.includes('archiveReturnFocusEl'), 'canvas-list.js 必须具备 archiveReturnFocusEl 焦点还原');
  assert.ok(canvasListJs.includes('trashBackdrop'), 'canvas-list.js 必须监听 trashBackdrop 关闭');
  assert.ok(canvasListJs.includes('archiveBackdrop'), 'canvas-list.js 必须监听 archiveBackdrop 关闭');
});

