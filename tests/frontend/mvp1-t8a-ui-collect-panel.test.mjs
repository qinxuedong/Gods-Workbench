// MVP1-T8a-UI 收集面板（issue #346）node 测试：
// 纯逻辑层直接 import 断言（mock adapter 状态机、URL 拦截、任务行渲染）；
// 浮窗结构、挂载入口与 CSS token 约束按仓库既有静态断言风格校验。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  COLLECT_ERROR_COPY,
  createMockCollectAdapter,
  isValidCollectUrl,
  renderCollectJobItemHtml,
} from '../../web/js/modules/asset-collect-panel.js';

const repoRoot = process.cwd();
const read = relativePath => readFileSync(path.join(repoRoot, relativePath), 'utf8');

const panelSource = read('web/js/modules/asset-collect-panel.js');
const managerSource = read('web/js/modules/asset-manager.js');
const collectCss = read('web/css/pages/asset-collect.css');

test('AC4 空 URL 与非 http(s) 链接被前端拦截', () => {
  assert.equal(isValidCollectUrl(''), false);
  assert.equal(isValidCollectUrl('   '), false);
  assert.equal(isValidCollectUrl(null), false);
  assert.equal(isValidCollectUrl('不是链接'), false);
  assert.equal(isValidCollectUrl('ftp://example.com/video.mp4'), false);
  assert.equal(isValidCollectUrl('blob:https://example.com/uuid'), false);
  assert.equal(isValidCollectUrl('data:text/html,hi'), false);
  assert.equal(isValidCollectUrl('javascript:alert(1)'), false);
  assert.equal(isValidCollectUrl('https://example.com/video.mp4'), true);
  assert.equal(isValidCollectUrl('http://example.com/page'), true);
});

test('AC1 mock adapter：URL 提交返回 queued 并随推进到 done', async () => {
  const adapter = createMockCollectAdapter({ autoTick: false });
  try {
    const created = await adapter.submitUrl('https://example.com/video.mp4', 'pair-token');
    assert.ok(created.jobId, '提交应返回稳定 job_id');
    assert.equal(created.status, 'queued');

    let job = await adapter.getJob(created.jobId);
    assert.equal(job.status, 'queued');
    assert.equal(job.progress, 0);

    assert.equal(adapter.advance(), true);
    job = await adapter.getJob(created.jobId);
    assert.equal(job.status, 'running');
    assert.ok(job.progress > 0 && job.progress < 100);

    for (let i = 0; i < 10 && job.status !== 'done'; i += 1) adapter.advance();
    job = await adapter.getJob(created.jobId);
    assert.equal(job.status, 'done');
    assert.equal(job.progress, 100);
  } finally {
    adapter.dispose();
  }
});

test('AC3 mock adapter：drm/blob 提交按契约错误码失败', async () => {
  const adapter = createMockCollectAdapter({ autoTick: false });
  try {
    await assert.rejects(
      adapter.submitUrl('https://drm.example.com/video', 'pair-token'),
      error => error.errorCode === 'drm_protected',
    );
    await assert.rejects(
      adapter.submitUrl('https://cdn.example.com/blob/master', 'pair-token'),
      error => error.errorCode === 'blob_url',
    );
  } finally {
    adapter.dispose();
  }
});

test('AC3 自定义 failFor 可注入失败且不影响其他 URL', async () => {
  const adapter = createMockCollectAdapter({ autoTick: false, failFor: url => (url.includes('blocked') ? 'unauthorized' : '') });
  try {
    await assert.rejects(
      adapter.submitUrl('https://example.com/blocked', 'pair-token'),
      error => error.errorCode === 'unauthorized',
    );
    const created = await adapter.submitUrl('https://example.com/ok', 'pair-token');
    assert.ok(created.jobId);
  } finally {
    adapter.dispose();
  }
});

test('AC2 mock adapter：多文件提交返回每个文件名与大小', async () => {
  const adapter = createMockCollectAdapter({ autoTick: false });
  try {
    const created = await adapter.submitFiles(
      [
        { name: 'hero.png', size: 1024 },
        { name: 'clip.mp4', size: 2048 },
      ],
      'pair-token',
    );
    assert.ok(created.jobId);
    assert.deepEqual(created.accepted, [
      { name: 'hero.png', size: 1024 },
      { name: 'clip.mp4', size: 2048 },
    ]);
  } finally {
    adapter.dispose();
  }
});

test('AC1 任务行渲染：queued 徽标与进度条可见，done 收尾', () => {
  const queued = renderCollectJobItemHtml({ jobId: 'job-1', title: 'https://example.com/video.mp4', status: 'queued', progress: 0 });
  assert.match(queued, /data-collect-job-id="job-1"/);
  assert.match(queued, /is-queued/);
  assert.match(queued, /排队中/);
  assert.match(queued, /role="progressbar"/);

  const done = renderCollectJobItemHtml({ jobId: 'job-2', title: 'https://example.com/video.mp4', status: 'done', progress: 100 });
  assert.match(done, /is-done/);
  assert.match(done, /完成/);
});

test('AC2 任务行渲染：多文件任务展示每个文件名与大小', () => {
  const html = renderCollectJobItemHtml({
    jobId: 'job-3',
    title: '本地文件 ×2',
    files: [
      { name: 'hero.png', size: 1024 },
      { name: 'clip.mp4', size: 2048 },
    ],
    status: 'running',
    progress: 40,
  });
  assert.match(html, /hero\.png/);
  assert.match(html, /1\.0 KB/);
  assert.match(html, /clip\.mp4/);
  assert.match(html, /2\.0 KB/);
});

test('AC3 失败任务行展示明确文案且不提供重试入口', () => {
  const failed = renderCollectJobItemHtml({ jobId: 'job-4', title: 'https://drm.example.com/video', status: 'failed', progress: 0, errorCode: 'drm_protected' });
  assert.match(failed, /is-failed/);
  assert.match(failed, /data-collect-error/);
  assert.ok(failed.includes(COLLECT_ERROR_COPY.drm_protected), '应展示 DRM 明确失败文案');
  assert.doesNotMatch(failed, /重试/);
  assert.doesNotMatch(failed, /<button/);
});

test('GW-UI-FW-01 浮窗规范：显式关闭、Escape、点外关闭与焦点闭环', () => {
  assert.match(panelSource, /data-collect-close/);
  assert.match(panelSource, /aria-modal="true"/);
  assert.match(panelSource, /role="dialog"/);
  assert.match(panelSource, /event\.key === 'Escape'/);
  assert.match(panelSource, /event\.target === overlay\)\s*close\(\)/);
  assert.match(panelSource, /lastTrigger\?\.focus\?\.\(\)/);
  assert.match(panelSource, /event\.key === 'Tab'/);
  assert.match(panelSource, /destroy\(\)/);
});

test('浮窗接入全局 FloatingDismissal 契约，面板内点击与 Escape 不被吞（QA 回归）', () => {
  // role="dialog" 会被 floating-dismissal.js 自动登记为浮层；必须显式声明
  // 内容边界与关闭语义，否则捕获阶段把面板内点击当"外部"吞掉。
  assert.match(panelSource, /data-floating-surface="asset-collect-panel"/);
  assert.match(panelSource, /data-floating-content="self"/);
  assert.match(panelSource, /data-floating-dismiss="outside"/);
  assert.match(panelSource, /data-floating-escape="close-only"/);
  assert.match(panelSource, /data-collect-close data-floating-close/);
});

test('遮罩 hidden 属性不被 display:flex 覆盖（QA 回归）', () => {
  assert.match(collectCss, /\.asset-collect-overlay\[hidden\]\s*\{[^}]*display:\s*none\s*!important/);
});

test('收集面板挂载入口与样式注入已接入资产工作台', () => {
  assert.match(managerSource, /import \{openAssetCollectOverlay\} from '\.\/asset-collect-panel\.js';/);
  assert.match(managerSource, /data-asset-collect-open/);
  assert.match(managerSource, /openAssetCollectOverlay\(event\.target\.closest\('\[data-asset-collect-open\]'\)\)/);
  assert.match(panelSource, /ensureAssetCollectStyles/);
  assert.match(panelSource, /\/static\/css\/pages\/asset-collect\.css/);
});

test('AC5 样式全部使用主题 token，无裸色值，含浅色主题与减少动效规则', () => {
  assert.match(collectCss, /var\(--gw-/);
  assert.match(collectCss, /html\[data-theme="light"\]/);
  assert.match(collectCss, /@media \(prefers-reduced-motion: reduce\)/);
  assert.doesNotMatch(collectCss, /:\s*#[0-9a-fA-F]{3,8}\b/);
});

test('node 测试清单已登记本文件', () => {
  const manifest = read('tests/frontend/node-files.txt').replace(/\r/g, '');
  assert.ok(
    manifest.split('\n').includes('tests/frontend/mvp1-t8a-ui-collect-panel.test.mjs'),
    'tests/frontend/node-files.txt 必须登记 mvp1-t8a-ui-collect-panel.test.mjs',
  );
});
