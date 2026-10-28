// QIN-437（issue #386）收集来源溯源展示 + 按来源删除入口 node 测试：
// AC1 展示来源 URL / 页面标题 / 收集时间三项，缺失显示「未知」且不报错；
// AC2 来源 URL 为纯文本（全部转义、不产生 href），防 XSS；
// AC3 「删除本次收集」走既有回收站接口（复用 openRegistryRecycleDialog，不新建永久删除）。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  COLLECT_DELETE_ATTRIBUTE,
  COLLECT_UNKNOWN_TEXT,
  formatCollectedAt,
  isCollectedAsset,
  normalizeCollectSources,
  renderCollectProvenance,
  resolveCollectDeleteAssetId,
} from '../../web/js/modules/asset-manager/collect-provenance.js';

const repoRoot = process.cwd();
const read = relativePath => readFileSync(path.join(repoRoot, relativePath), 'utf8');

const collectedItem = {
  id: 'ast_collect_1',
  metadata: {
    collect_sha256: 'a'.repeat(64),
    collect_sources: [
      { url: 'https://example.com/media/clip.mp4', title: '示例片段', captured_at: '2026-10-28T07:18:56Z' },
    ],
  },
};

// node:test 环境无 DOM，用最小 stub 模拟事件委托的 target.closest 链路。
function fakeEvent(assetId) {
  const element = { getAttribute: name => (name === COLLECT_DELETE_ATTRIBUTE ? assetId : null) };
  return { target: { closest: selector => (selector === `[${COLLECT_DELETE_ATTRIBUTE}]` ? element : null) } };
}

test('AC1 收集资产详情展示三项来源字段（URL / 标题 / 收集时间）', () => {
  const html = renderCollectProvenance(collectedItem);
  assert.ok(html.includes('收集来源'), '应渲染收集来源区块');
  for (const label of ['来源 URL', '页面标题', '收集时间']) {
    assert.ok(html.includes(`<span>${label}</span>`), `应展示字段 ${label}`);
  }
  assert.ok(html.includes('https://example.com/media/clip.mp4'), '应展示来源 URL');
  assert.ok(html.includes('示例片段'), '应展示页面标题');
  assert.ok(html.includes(formatCollectedAt('2026-10-28T07:18:56Z')), '应展示格式化后的收集时间');
});

test('AC1 单条来源时间按本地时区展示到分钟；非法时间原样回显', () => {
  assert.match(formatCollectedAt('2026-10-28T07:18:56Z'), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  assert.equal(formatCollectedAt('不是时间'), '不是时间');
  assert.equal(formatCollectedAt(''), null);
  assert.equal(formatCollectedAt(null), null);
  assert.equal(formatCollectedAt(undefined), null);
});

test('AC1 字段缺失显示「未知」且不抛异常', () => {
  const html = renderCollectProvenance({
    id: 'ast_collect_2',
    metadata: { collect_sources: [{}, { url: '', title: '   ', captured_at: null }] },
  });
  assert.ok(html.includes(COLLECT_UNKNOWN_TEXT));
  assert.equal((html.match(new RegExp(`>${COLLECT_UNKNOWN_TEXT}<`, 'g')) || []).length >= 3, true, '三项缺失均显示未知');
  assert.ok(html.includes('收集来源'));
});

test('AC1 仅有收集哈希、来源数组缺失时仍展示区块并标未知', () => {
  const html = renderCollectProvenance({ id: 'ast_collect_3', metadata: { collect_sha256: 'b'.repeat(64) } });
  assert.ok(html.includes('收集来源'));
  assert.ok(html.includes(COLLECT_UNKNOWN_TEXT));
});

test('AC1 异常输入不抛异常：非收集资产返回空串，非法结构安全降级', () => {
  for (const bad of [null, undefined, {}, { id: 'x' }, { id: 'x', metadata: null }, { id: 'x', metadata: 'meta' }, { id: 'x', metadata: [] }]) {
    assert.equal(renderCollectProvenance(bad), '');
    assert.equal(isCollectedAsset(bad), false);
  }
  assert.deepEqual(normalizeCollectSources(null), []);
  assert.deepEqual(normalizeCollectSources({ metadata: { collect_sources: 'x' } }), []);
  assert.deepEqual(
    normalizeCollectSources({ metadata: { collect_sources: [null, 1, 'x', [], { url: ' https://a.test/1 ' }] } }),
    [{ url: 'https://a.test/1', title: null, capturedAt: null }],
    '非法条目跳过，合法条目去空白',
  );
});

test('AC1 多次收集同一内容时逐条展示全部来源', () => {
  const html = renderCollectProvenance({
    id: 'ast_collect_4',
    metadata: {
      collect_sources: [
        { url: 'https://a.test/1', title: '第一条', captured_at: '2026-10-28T01:00:00Z' },
        { url: 'https://b.test/2', title: '第二条', captured_at: '2026-10-28T02:00:00Z' },
      ],
    },
  });
  assert.ok(html.includes('data-collect-source-index="0"'));
  assert.ok(html.includes('data-collect-source-index="1"'));
  assert.ok(html.includes('第一条') && html.includes('第二条'));
});

test('AC2 来源 URL 为纯文本：不输出 href/a 标签，脚本与属性注入全部转义', () => {
  const html = renderCollectProvenance({
    id: 'ast_collect_5',
    metadata: {
      collect_sources: [
        { url: 'javascript:alert(1)', title: '<script>alert(2)</script>', captured_at: '2026-10-28T07:18:56Z' },
        { url: '"><img src=x onerror=alert(3)>', title: '"><svg onload=alert(4)>', captured_at: '2026-10-28T07:18:56Z' },
      ],
    },
  });
  assert.ok(!/href=/i.test(html), '不得输出 href 属性');
  assert.ok(!/<a\s/i.test(html), '不得输出 a 标签');
  assert.ok(!html.includes('<script'), '不得出现原始 <script');
  assert.ok(!html.includes('<img'), '不得出现原始 <img');
  assert.ok(!html.includes('<svg'), '不得出现原始 <svg');
  assert.ok(html.includes('&lt;script&gt;'), 'script 标签应被转义为纯文本');
  assert.ok(html.includes('javascript:alert(1)'), '伪协议仅作为纯文本展示');
});

test('AC3 删除入口只分发 asset_id，不在渲染层发请求', () => {
  const html = renderCollectProvenance(collectedItem);
  assert.ok(html.includes(`${COLLECT_DELETE_ATTRIBUTE}="ast_collect_1"`), '删除入口应携带 asset_id');
  assert.ok(html.includes('删除本次收集'));
  assert.equal(resolveCollectDeleteAssetId(fakeEvent('ast_collect_1')), 'ast_collect_1');
  assert.equal(resolveCollectDeleteAssetId(fakeEvent('  ')), '', '空 ID 不分发');
  assert.equal(resolveCollectDeleteAssetId({ target: { closest: () => null } }), '');
  assert.equal(resolveCollectDeleteAssetId({}), '');
  assert.equal(resolveCollectDeleteAssetId(null), '');
  // 无 asset_id 时不得渲染无目标删除入口。
  const noId = renderCollectProvenance({ metadata: { collect_sources: [{ url: 'https://a.test/1' }] } });
  assert.ok(!noId.includes(COLLECT_DELETE_ATTRIBUTE));
  const moduleSource = read('web/js/modules/asset-manager/collect-provenance.js');
  for (const forbidden of ['fetch(', 'XMLHttpRequest', 'method:', 'DELETE ', '/api/']) {
    assert.ok(!moduleSource.includes(forbidden), `渲染模块不得直接发起请求: ${forbidden}`);
  }
});

test('AC3 接线：详情挂载收集来源，删除复用既有回收站链路', () => {
  const managerSource = read('web/js/modules/asset-manager.js');
  assert.ok(
    managerSource.includes("from './asset-manager/collect-provenance.js'"),
    '应导入 collect-provenance 模块',
  );
  const fnStart = managerSource.indexOf('function renderAssetDetail(item)');
  assert.ok(fnStart > 0, '应存在 renderAssetDetail');
  const fnEnd = managerSource.indexOf('function renderPromptManager()', fnStart);
  const detailFn = managerSource.slice(fnStart, fnEnd);
  assert.ok(detailFn.includes('${renderCollectProvenance(item)}'), '资产详情应挂载收集来源区块');

  assert.ok(
    managerSource.includes('const collectDeleteAssetId = resolveCollectDeleteAssetId(event);'),
    '点击委托应解析收集删除入口',
  );
  assert.ok(
    managerSource.includes('if(collectDeleteAssetId){ await openRegistryRecycleDialog(collectDeleteAssetId); return; }'),
    '收集删除应复用既有回收站确认流程',
  );

  // 既有回收站链路：openRegistryRecycleDialog → recycleRegistryAsset → deleteRegistryAsset。
  const recycleStart = managerSource.indexOf('async function recycleRegistryAsset(');
  assert.ok(recycleStart > 0, '应存在既有回收站删除实现');
  const recycleBody = managerSource.slice(recycleStart, managerSource.indexOf('async function confirmRegistryRecycle', recycleStart));
  assert.ok(
    recycleBody.includes('assetManagerApi.deleteRegistryAsset('),
    '删除必须走既有回收站接口',
  );
  assert.ok(!/hard_delete|permanent_delete|permanentDelete/i.test(managerSource), '不得引入永久删除');
  const apiSource = read('web/js/modules/asset-manager/api.js');
  assert.ok(
    apiSource.includes("deleteRegistryAsset(assetId, expectedVersion, init = {})"),
    '既有回收站删除接口保持唯一入口',
  );
});

test('AC4 纯原生实现：无框架、无 CDN、import 均为仓库内相对模块', () => {
  const stripLicenseHeader = source => source.replace(/^\/\*[\s\S]*?\*\//, '');
  const moduleSource = stripLicenseHeader(read('web/js/modules/asset-manager/collect-provenance.js'));
  assert.ok(!/https?:\/\//.test(moduleSource), '模块不得包含外部 URL');
  assert.ok(!/\brequire\s*\(/.test(moduleSource), '不得使用 CommonJS');
  assert.ok(!/\bdocument\.|\bwindow\./.test(moduleSource), '渲染模块不得直接操作 DOM 全局');
  const managerSource = read('web/js/modules/asset-manager.js');
  const imports = [
    ...[...managerSource.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"];?\s*$/gm)].map(m => m[1]),
    ...[...managerSource.matchAll(/\bimport\s*\(\s*['"]([^'"]+)['"]/g)].map(m => m[1]),
  ];
  assert.ok(imports.length > 0, '应能解析 import 清单');
  for (const specifier of imports) {
    assert.ok(specifier.startsWith('.'), `import 必须为仓库内相对模块: ${specifier}`);
  }
});

test('测试文件已登记 tests/frontend/node-files.txt（缺失则 CI 红）', () => {
  const listed = read('tests/frontend/node-files.txt');
  assert.ok(
    listed.includes('tests/frontend/mvp1-collect-provenance.test.mjs'),
    'node-files.txt 应登记本测试',
  );
});
