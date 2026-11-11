// QIN-587: 收集箱嵌入金库网格收为零宽首列与单内容列，消除左右空轨道与窄屏空行
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const managerJs = readAssetManagerAggregate();

test('AC1: vault 嵌入态收集箱宽屏网格首列收为零宽且无详情轨道，消除 240px 左空列与 280-320px 右空轨道', () => {
  // 必须存在针对 .asset-manager-root-page-inbox 的零宽首列规则，且仅单内容列吃满余宽
  assert.match(
    vaultCss,
    /html\.vault-shell-embedded body\.gw-signal\.gw-page--asset-manager #assetManagerRoot\.asset-manager-root-page-inbox\s*\{\s*\/\* 【QIN-587】[^]*?\*\/\s*grid-template-columns:\s*0\s+minmax\(0,1fr\)\s*!important;/
  );
  // 三列兜底规则选择器链必须排除 .asset-manager-root-page-inbox
  assert.match(
    vaultCss,
    /#assetManagerRoot:not\(\.asset-manager-root-registry\):not\(\.asset-manager-root-library\):not\(\.asset-manager-root-page-scripts\):not\(\.asset-manager-root-page-projects\):not\(\.asset-manager-root-page-prompts\):not\(\.asset-manager-root-page-lut\):not\(\.asset-manager-root-page-canvas-assets\):not\(\.asset-manager-root-page-workflows\):not\(\.asset-manager-root-page-inbox\)/
  );
});

test('AC2: 约 900px 宽度上下及更窄屏时，收集箱收为单列紧凑 3 行，显式覆盖宽屏零宽首列以绝掉入 0 宽列', () => {
  // 针对 ≤900px 窄屏，必须显式覆盖 grid-template-columns 为单列 minmax(0,1fr)，避免宽屏零宽规则因特异性滞留
  assert.match(
    vaultCss,
    /@media\s*\(max-width:\s*900px\)[\s\S]*?#assetManagerRoot\.asset-manager-root-page-inbox\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,1fr\)\s*!important;/
  );
  // 窄屏模板行收为 3 行：auto auto minmax(360px,1fr)
  assert.match(
    vaultCss,
    /@media\s*\(max-width:\s*900px\)[\s\S]*?#assetManagerRoot\.asset-manager-root-page-inbox\s*\{[\s\S]*?grid-template-rows:\s*auto\s+auto\s+minmax\(360px,1fr\)\s*!important;/
  );
  // 窄屏下收集箱公共工具栏与统计条明确落入单列 column: 1
  assert.match(
    vaultCss,
    /@media\s*\(max-width:\s*900px\)[\s\S]*?#assetManagerRoot\.asset-manager-root-page-inbox\s*>\s*:is\(\.registry-overview-shell,\.registry-toolbar\)\s*\{\s*grid-column:\s*1\s*!important;\s*\}/
  );
  // 收集箱内容区在 900px 下明确落入 column: 1 且提升至第 3 行，避免落入留给不存在左栏的第 4 行
  assert.match(
    vaultCss,
    /@media\s*\(max-width:\s*900px\)[\s\S]*?#assetManagerRoot\.asset-manager-root-page-inbox\s*>\s*:is\(\.asset-panel\.asset-content,\.inbox-content\)\s*\{\s*grid-column:\s*1\s*!important;\s*grid-row:\s*3\s*!important;\s*\}/
  );
});

test('AC2-层叠仿真: 模拟 900px 媒体查询生效时的特异性与层叠顺序，断言单列绝对胜出', () => {
  // 提取针对 #assetManagerRoot.asset-manager-root-page-inbox 的所有 grid-template-columns 规则
  const regex = /([^{}]+)\{\s*([^}]+)\s*\}/g;
  const colDeclarations = [];
  let match;
  while ((match = regex.exec(vaultCss)) !== null) {
    const sel = match[1].trim();
    const body = match[2];
    if (sel.includes('#assetManagerRoot.asset-manager-root-page-inbox') && body.includes('grid-template-columns')) {
      const colMatch = body.match(/grid-template-columns:\s*([^;!]+)(!important)?/);
      if (colMatch) {
        colDeclarations.push({
          selector: sel,
          value: colMatch[1].trim(),
          important: Boolean(colMatch[2]),
          index: match.index,
        });
      }
    }
  }

  assert.ok(colDeclarations.length >= 2, '至少应存在宽屏默认与窄屏媒体查询两条列规则');
  const wideRule = colDeclarations[0];
  const narrowRule = colDeclarations[colDeclarations.length - 1];

  // 宽屏规则声明零宽首列
  assert.equal(wideRule.value, '0 minmax(0,1fr)');
  assert.equal(wideRule.important, true);

  // 窄屏规则在源码位置更靠后且声明单列
  assert.equal(narrowRule.value, 'minmax(0,1fr)');
  assert.equal(narrowRule.important, true);
  assert.ok(narrowRule.index > wideRule.index, '窄屏覆盖规则必须在宽屏规则之后声明（源码顺序胜出）');

  // 计算选择器特异性 (Specificity) 确保窄屏选择器权重不低于宽屏
  const getSpecificity = (sel) => {
    const ids = (sel.match(/#[a-zA-Z0-9_-]+/g) || []).length;
    const classes = (sel.match(/\.[a-zA-Z0-9_-]+/g) || []).length;
    const tags = (sel.match(/(^|\s)[a-zA-Z0-9_-]+/g) || []).length;
    return ids * 100 + classes * 10 + tags;
  };
  const wideSpec = getSpecificity(wideRule.selector);
  const narrowSpec = getSpecificity(narrowRule.selector);
  assert.ok(
    narrowSpec >= wideSpec,
    `窄屏规则选择器特异性 (${narrowSpec}) 不得低于宽屏规则 (${wideSpec})，否则单列无法生效`
  );
});

test('AC3: 资产总览、项目、剧本、提示词、画布、工作流、LUT 等既有视图布局规则无回归', () => {
  // 总览保持 0 minmax(0,1fr)
  assert.match(vaultCss, /#assetManagerRoot\.asset-manager-root-registry \{\s*\/\* 【QIN-571】[^]*?\*\/\s*grid-template-columns: 0 minmax\(0,1fr\) !important;/);
  // 项目保持 0 minmax(0,1fr)
  assert.match(vaultCss, /#assetManagerRoot\.asset-manager-root-page-projects \{\s*\/\* 【QIN-572】[^]*?\*\/\s*grid-template-columns: 0 minmax\(0,1fr\) !important;/);
  // 剧本保持单列 minmax(0,1fr)
  assert.match(vaultCss, /#assetManagerRoot\.asset-manager-root-page-scripts\s*\{\s*\/\* 【QIN-569 AC2】[^]*?\*\/\s*grid-template-columns: minmax\(0,1fr\) !important;/);
  // 提示词/画布/工作流/LUT 保持保留详情抽屉列
  assert.match(vaultCss, /#assetManagerRoot:is\(\.asset-manager-root-page-prompts,\.asset-manager-root-page-lut,\.asset-manager-root-page-canvas-assets,\.asset-manager-root-page-workflows\)\s*\{\s*\/\* 【QIN-574】[^]*?\*\/\s*grid-template-columns:\s*0\s+minmax\(0,1fr\)\s+minmax\(280px,320px\)\s*!important;/);
});

test('AC4: 不得使用 display:none 隐藏不存在的树来假装消除空列', () => {
  // 检查是否新增了针对 inbox-tree 或假装隐藏树的不当 display:none
  assert.doesNotMatch(vaultCss, /\.asset-manager-root-page-inbox\s*>\s*:is\(\.registry-nav,\.asset-nav\)\s*\{\s*display:\s*none/);
  // 收集箱在嵌入态下由 renderAssetTreePanel 保持原生不渲染树
  assert.match(managerJs, /if\(document\.documentElement\.classList\.contains\('vault-shell-embedded'\)\)\s*return '';/);
});
