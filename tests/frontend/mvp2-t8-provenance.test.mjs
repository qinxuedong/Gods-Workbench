// MVP2-T8（issue #383）资产详情 provenance 链展示 node 测试：
// AC1 完整链按序渲染且节点经 mock 回调可导航；
// AC2 字段缺失显示「未知」、异常输入不抛未捕获异常；
// AC3 动态文本全部转义防 XSS、不产生可跳转 URL；
// AC4 纯原生实现，无框架/CDN/外部 URL 依赖。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  PROVENANCE_UNKNOWN_TEXT,
  normalizeProvenance,
  provenanceNodes,
  renderProvenanceChain,
  setProvenanceNavigateHandler,
  getProvenanceNavigateHandler,
  handleProvenanceNodeClick,
} from '../../web/js/modules/asset-manager/provenance.js';

const repoRoot = process.cwd();
const read = relativePath => readFileSync(path.join(repoRoot, relativePath), 'utf8');

const fullItem = {
  asset_id: 'out-1',
  provenance: { version: 'v1', task_id: 't-1', shot_id: 's-1', reference_asset_ids: ['a-1', 'a-2'] },
};

// node:test 环境无 DOM，用最小 stub 模拟事件委托的 target.closest 链路。
function fakeEvent(kind, id) {
  const node = { getAttribute: name => (name === 'data-prov-kind' ? kind : id) };
  return { target: { closest: selector => (selector === '[data-prov-kind]' ? node : null) } };
}

test('AC1 完整链按序渲染五环（成片→版本→任务→镜头→参考素材）', () => {
  const html = renderProvenanceChain(fullItem);
  assert.ok(html.includes('来源链'), '应渲染来源链区块');
  let last = -1;
  for (const kind of ['asset', 'version', 'task', 'shot', 'reference']) {
    const idx = html.indexOf(`data-prov-kind="${kind}"`);
    assert.ok(idx > last, `节点 ${kind} 应按链序出现`);
    last = idx;
  }
  for (const id of ['out-1', 'v1', 't-1', 's-1', 'a-1', 'a-2']) {
    assert.ok(html.includes(id), `应展示节点 ID ${id}`);
  }
});

test('AC1 已知节点带 data-prov-id 可导航，未知节点不可导航', () => {
  const html = renderProvenanceChain(fullItem);
  assert.ok(html.includes('data-prov-kind="task" data-prov-id="t-1"'));
  const partial = renderProvenanceChain({ asset_id: 'out-1', provenance: { version: 'v1', reference_asset_ids: [] } });
  assert.ok(partial.includes('data-prov-kind="task"'), '未知环保留 kind 标记');
  assert.ok(!partial.includes('data-prov-kind="task" data-prov-id='), '未知节点不得携带可导航 id');
});

test('AC1 mock 回调：点击已知节点分发 (kind, id)', () => {
  const calls = [];
  setProvenanceNavigateHandler((kind, id) => calls.push([kind, id]));
  try {
    assert.equal(getProvenanceNavigateHandler() !== null, true);
    assert.equal(handleProvenanceNodeClick(fakeEvent('task', 't-1')), true);
    assert.equal(handleProvenanceNodeClick(fakeEvent('reference', 'a-2')), true);
    assert.deepEqual(calls, [['task', 't-1'], ['reference', 'a-2']]);
  } finally {
    setProvenanceNavigateHandler(null);
  }
});

test('AC1 未命中/未知节点/异常事件/未注册回调时不分发且不抛异常', () => {
  const calls = [];
  setProvenanceNavigateHandler((kind, id) => calls.push([kind, id]));
  try {
    assert.equal(handleProvenanceNodeClick({ target: { closest: () => null } }), false);
    assert.equal(handleProvenanceNodeClick(fakeEvent('shot', '')), false);
    assert.equal(handleProvenanceNodeClick({}), false);
    assert.equal(handleProvenanceNodeClick(null), false);
    assert.deepEqual(calls, []);
  } finally {
    setProvenanceNavigateHandler(null);
  }
  assert.equal(getProvenanceNavigateHandler(), null);
  assert.equal(handleProvenanceNodeClick(fakeEvent('task', 't-1')), false);
  assert.deepEqual(calls, []);
});

test('AC2 缺失字段各环显示「未知」且不计入可导航节点', () => {
  const html = renderProvenanceChain({ asset_id: 'out-1', provenance: { version: 'v1', reference_asset_ids: [] } });
  assert.equal((html.match(/prov-node-unknown/g) || []).length, 3, '任务/镜头/参考素材三环未知');
  for (const label of ['任务', '镜头', '参考素材']) {
    assert.ok(html.includes(`${label}：${PROVENANCE_UNKNOWN_TEXT}`), `${label} 环应显示未知`);
  }
  assert.ok(html.includes('成片：out-1'));
  assert.ok(html.includes('版本：v1'));
});

test('AC2 异常输入不抛异常：链缺失或非法时返回空串', () => {
  for (const bad of [null, undefined, {}, { asset_id: 'x' }, { asset_id: 'x', provenance: null }, { asset_id: 'x', provenance: '链' }, { asset_id: 'x', provenance: [] }, { asset_id: 'x', provenance: 42 }]) {
    assert.equal(renderProvenanceChain(bad), '');
  }
  assert.equal(normalizeProvenance(null), null);
  assert.deepEqual(provenanceNodes(undefined), []);
});

test('AC2 宽松容错：数值标量转文本、参考素材只保留非空字符串', () => {
  const nodes = provenanceNodes({
    asset_id: 'out-1',
    provenance: { version: 'v1', task_id: 123, shot_id: '  s-9  ', reference_asset_ids: ['a-1', '', '   ', 42, null] },
  });
  assert.equal(nodes.find(n => n.kind === 'task').id, '123');
  assert.equal(nodes.find(n => n.kind === 'shot').id, 's-9');
  assert.deepEqual(nodes.filter(n => n.kind === 'reference').map(n => n.id), ['a-1']);
});

test('AC2 version 兼容 QIN-440 契约字段 artifact_version_id', () => {
  const nodes = provenanceNodes({
    asset_id: 'out-1',
    provenance: { artifact_version_id: 'ver-9', task_id: 't-1', shot_id: 's-1', reference_asset_ids: ['a-1'] },
  });
  assert.equal(nodes.find(n => n.kind === 'version').id, 'ver-9');
  const nodesWithBoth = provenanceNodes({
    asset_id: 'out-1',
    provenance: { version: 'v2', artifact_version_id: 'ver-9', task_id: 't-1', shot_id: 's-1', reference_asset_ids: [] },
  });
  assert.equal(nodesWithBoth.find(n => n.kind === 'version').id, 'v2');
});

test('AC3 动态文本全部转义：无原始 script/img 注入', () => {
  const html = renderProvenanceChain({
    asset_id: 'out-1',
    provenance: { version: 'v1', task_id: '<script>alert(1)</script>', shot_id: '"><img src=x onerror=alert(2)>', reference_asset_ids: ['a-1'] },
  });
  assert.ok(!html.includes('<script'), '不得出现原始 <script');
  assert.ok(html.includes('&lt;script&gt;'), 'script 标签应被转义为纯文本');
  assert.ok(!html.includes('<img'), '不得出现原始 <img');
  assert.ok(html.includes('&lt;img'), 'img 标签应被转义为纯文本');
});

test('AC3 不产生任何可跳转链接：无 href，伪协议仅纯文本展示', () => {
  const html = renderProvenanceChain({
    asset_id: 'out-1',
    provenance: { version: 'v1', task_id: 't-1', shot_id: 's-1', reference_asset_ids: ['javascript:alert(3)'] },
  });
  assert.ok(!/href=/i.test(html), '来源链不得输出 href 属性');
  assert.ok(html.includes('javascript:alert(3)'), '伪协议 ID 仅作为纯文本展示');
  const node = { getAttribute: name => (name === 'data-prov-kind' ? 'reference' : 'javascript:alert(3)') };
  const calls = [];
  setProvenanceNavigateHandler((kind, id) => calls.push([kind, id]));
  try {
    assert.equal(handleProvenanceNodeClick({ target: { closest: () => node } }), true);
    assert.deepEqual(calls, [['reference', 'javascript:alert(3)']], '点击只分发文本，不执行 URL');
  } finally {
    setProvenanceNavigateHandler(null);
  }
});

test('AC4 模块与挂载均为纯原生：无框架/CDN/外部 URL', () => {
  // 许可证头注释中的 apache.org 链接不属于运行时资源，剥除后再断言。
  const stripLicenseHeader = source => source.replace(/^\/\*[\s\S]*?\*\//, '');
  const provenanceSource = stripLicenseHeader(read('web/js/modules/asset-manager/provenance.js'));
  assert.ok(!/https?:\/\//.test(provenanceSource), 'provenance 模块不得包含外部 URL');
  assert.ok(!/\brequire\s*\(/.test(provenanceSource), '不得使用 CommonJS');
  const managerSource = read('web/js/modules/asset-manager.js');
  // 静态 import 独立成行；动态 import 单独匹配，均须为仓库内相对模块。
  const imports = [
    ...[...managerSource.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"];?\s*$/gm)].map(m => m[1]),
    ...[...managerSource.matchAll(/\bimport\s*\(\s*['"]([^'"]+)['"]/g)].map(m => m[1]),
  ];
  assert.ok(imports.length > 0, '应能解析 import 清单');
  for (const specifier of imports) {
    assert.ok(specifier.startsWith('.'), `import 必须为仓库内相对模块: ${specifier}`);
  }
});

test('挂载接线：统一资产详情渲染来源链，事件委托接入导航分发', () => {
  const managerSource = read('web/js/modules/asset-manager.js');
  assert.ok(managerSource.includes("from './asset-manager/provenance.js'"), '应导入 provenance 模块');
  const fnStart = managerSource.indexOf('function renderAssetDetail(item)');
  assert.ok(fnStart > 0, '应存在 renderAssetDetail');
  const fnEnd = managerSource.indexOf('function renderPromptManager()', fnStart);
  const detailFn = managerSource.slice(fnStart, fnEnd);
  assert.ok(detailFn.includes('${renderProvenanceChain(item)}'), '资产详情应挂载来源链区块');
  assert.ok(managerSource.includes('if(handleProvenanceNodeClick(event)) return;'), '点击委托应接入 provenance 分发');
});

test('测试文件已登记 tests/frontend/node-files.txt（缺失则 CI 红）', () => {
  const listed = read('tests/frontend/node-files.txt');
  assert.ok(listed.includes('tests/frontend/mvp2-t8-provenance.test.mjs'), 'node-files.txt 应登记本测试');
});
