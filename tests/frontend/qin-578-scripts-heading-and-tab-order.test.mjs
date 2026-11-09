// QIN-578: 资产库剧本标签隐藏工具栏标题并将项目/剧本/提示词移至其他后
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const managerJs = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');

test('AC1: 嵌入态下剧本标签隐藏 content-heading，实现 Coverflow 与工具栏控件靠左对齐', () => {
  // 嵌入态 scripts 标签设置 options.hideHeading 并与 QIN-574 隐藏机制兼容
  assert.match(
    managerJs,
    /if\(embeddedVault && activeTab === 'scripts'\)\s*options\.hideHeading = true;/,
    '嵌入态剧本标签必须显式设置 options.hideHeading 消除 content-heading'
  );
  assert.match(
    managerJs,
    /const hideHeading = Boolean\(options\.hideHeading \|\| \(embeddedVault && \['prompts', 'canvas-assets', 'workflows'\]\.includes\(activeTab\)\)\);/,
    '保持与 QIN-574 prompts/canvas/workflows 契约一致'
  );
  // 工具栏必须同时满足 options.hideHeading 与内置 hideHeading 隐藏分支，并保留 options.hideHeading ? '' : 结构
  assert.match(
    managerJs,
    /\$\{options\.hideHeading \? '' : \(hideHeading \? '' : `<div class="content-heading">/,
    '工具栏渲染必须在 hideHeading 生效时输出空字符串'
  );
  // 保留历史 renderRegistryToolbar 调用签名与字面量契约
  assert.match(
    managerJs,
    /renderRegistryToolbar\(null,\s*toolbarTitle,\s*toolbarSubtitle,\s*\{allowUrl:true,hideHeading:activeTab === 'registry' \|\| activeTab === 'projects'\}\)/,
    'render() 中的旧字面量必须保持，以兼容历史 QIN-572 测试断言'
  );
});

test('AC2: 顶栏概览统计卡片顺序调整为 LUT → 画布 → 其他 → 项目 → 剧本 → 提示词', () => {
  const statsSlice = managerJs.slice(
    managerJs.indexOf('function renderRegistryOverviewStats'),
    managerJs.indexOf('function toggleRegistryFeature')
  );
  const lut = statsSlice.indexOf("{id:'lut'");
  const canvas = statsSlice.indexOf("{id:'reference-canvas'");
  const other = statsSlice.indexOf("{id:'other'");
  const projects = statsSlice.indexOf("{id:'projects', name:'项目', nav:true");
  const scripts = statsSlice.indexOf("{id:'scripts', name:'剧本', nav:true");
  const prompts = statsSlice.indexOf("{id:'prompts', name:'提示词', nav:true");

  for (const pos of [lut, canvas, other, projects, scripts, prompts]) {
    assert.ok(pos > 0, '所有关键统计卡片与导航入口必须存在于 stats 数组中');
  }

  assert.ok(
    lut < canvas && canvas < other && other < projects && projects < scripts && scripts < prompts,
    '卡片顺序必须为 LUT → 画布 → 其他 → 项目 → 剧本 → 提示词'
  );
});

test('AC3: 顶栏导航卡片保留 is-nav 与 data-registry-nav，点击激活对应系统看板', () => {
  assert.match(managerJs, /data-registry-nav=/);
  assert.match(managerJs, /is-nav/);
  const navClickBlock = managerJs.slice(
    managerJs.indexOf("data-registry-nav]')){"),
    managerJs.indexOf('const registryStat =')
  );
  assert.match(navClickBlock, /activateAssetBoard\(navBoard\)/);
  assert.match(navClickBlock, /saveAssetTreeState\(\)/);
});
