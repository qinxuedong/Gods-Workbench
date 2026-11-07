// QIN-571: 资产库嵌入金库页移除孤立面包屑头条与 240px 空列
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const managerCss = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

test('AC1: vault 嵌入态隐藏页头孤立面包屑条，独立打开不受影响', () => {
  // vault 作用域隐藏规则存在且带 !important（压过 signal-flow 的 50px 页头条）。
  assert.match(vaultCss, /html\.vault-shell-embedded body\.gw-signal\.gw-page--asset-manager \.asset-page-head \{ display: none !important; \}/);
  // asset-vault.css 内所有针对 .asset-page-head 的 display:none 都必须限定在 vault 嵌入作用域。
  const hiddenLines = vaultCss.split('\n').filter(line => line.includes('.asset-page-head') && line.includes('display: none'));
  assert.ok(hiddenLines.length > 0, '应存在 vault 嵌入态页头隐藏规则');
  for (const line of hiddenLines) {
    assert.ok(line.includes('vault-shell-embedded'), `页头隐藏规则必须限定 vault 作用域：${line.trim()}`);
  }
});

test('AC2: registry 网格首列收为零宽，收回 QIN-489 保留的 240px 保留列', () => {
  // 嵌入态 registry 专条：首列 0（沿用 QIN-566 在 asset-manager.css 的既定零宽模式），内容子项仍落第 2 列。
  assert.match(vaultCss, /#assetManagerRoot\.asset-manager-root-registry \{\s*\/\* 【QIN-571】[^]*?\*\/\s*grid-template-columns: 0 minmax\(0,1fr\) !important;/);
  // QIN-489 为已停用左栏保留 240px 的旧值不得回归（仅限 vault 作用域规则；非 vault 媒体查询规则服务独立打开模式，不在本票范围）。
  assert.doesNotMatch(vaultCss, /vault-shell-embedded[^{]*asset-manager-root-registry \{[^}]*240px/);
});

test('AC3: 非 vault 通用规则不受影响（独立打开 asset-manager.html 布局保持）', () => {
  // QIN-489 的 filters-collapsed 通用单列规则仍在，未被本票改动。
  assert.match(vaultCss, /body\.gw-signal\.gw-page--asset-manager #assetManagerRoot\.asset-manager-root-registry\.asset-registry-filters-collapsed \{ grid-template-columns: minmax\(0,1fr\) !important; \}/);
  // QIN-566 零宽模式仍限定嵌入态（asset-manager.css），非嵌入态没有该规则。
  assert.match(managerCss, /html\.vault-shell-embedded \.asset-manager-root\.asset-tree-layout[^{]*\{[^}]*grid-template-columns:0 minmax\(0,1fr\)/);
});
