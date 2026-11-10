// QIN-574: 资产库提示词与画布标签清除首列空栏并左对齐工具栏
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const managerJs = readAssetManagerAggregate();

test('AC1: vault 嵌入态提示词与画布资产视图首列收为零宽，消除 240px 空白列', () => {
  // 必须存在针对 prompts、canvas-assets 的零宽规则，且右侧保留 minmax(280px,320px) 抽屉列
  // 【QIN-580】LUT 库同为首列无内容的子页面，一并纳入这份清单。
  assert.match(
    vaultCss,
    /html\.vault-shell-embedded body\.gw-signal\.gw-page--asset-manager #assetManagerRoot:is\(\.asset-manager-root-page-prompts,\.asset-manager-root-page-lut,\.asset-manager-root-page-canvas-assets,\.asset-manager-root-page-workflows\)\s*\{\s*\/\* 【QIN-574】[^]*?\*\/\s*grid-template-columns:\s*0\s+minmax\(0,1fr\)\s+minmax\(280px,320px\)\s*!important;/
  );
  // 三列兜底选择器必须排除 prompts、LUT、canvas-assets 与 workflows
  assert.match(
    vaultCss,
    /#assetManagerRoot:not\(\.asset-manager-root-registry\):not\(\.asset-manager-root-library\):not\(\.asset-manager-root-page-scripts\):not\(\.asset-manager-root-page-projects\):not\(\.asset-manager-root-page-prompts\):not\(\.asset-manager-root-page-lut\):not\(\.asset-manager-root-page-canvas-assets\):not\(\.asset-manager-root-page-workflows\)/
  );
  // 详情抽屉收起时轨道同步收到 44px，不留 280px 空列
  assert.match(
    vaultCss,
    /#assetManagerRoot\.asset-manager-root-page-lut\.registry-detail-collapsed\s*\{\s*\/\* 【QIN-580】[^]*?\*\/\s*grid-template-columns:\s*0\s+minmax\(0,1fr\)\s+44px\s*!important;/
  );
});

test('AC2: 嵌入态下提示词与画布标签隐藏 content-heading，实现工具栏控件靠左对齐', () => {
  assert.match(
    managerJs,
    /const hideHeading = Boolean\(options\.hideHeading \|\| \(embeddedVault && \['prompts', 'canvas-assets', 'workflows'\]\.includes\(activeTab\)\)\);/
  );
  assert.match(
    managerJs,
    /\$\{options\.hideHeading \? '' : \(hideHeading \? '' : `<div class="content-heading">/
  );
});

test('AC3: 历史 QIN-572 项目视图兼容性不断裂', () => {
  assert.match(
    managerJs,
    /hideHeading:activeTab === 'registry' \|\| activeTab === 'projects'/
  );
});
