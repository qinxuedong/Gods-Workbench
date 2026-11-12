// QIN-578: 资产库剧本标签隐藏工具栏标题并将项目/剧本/提示词移至其他后
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';
// QIN-602：工具栏 hideHeading 契约由源码正则改为等价行为断言（真实执行渲染函数）。
import {createAssetViewHarness} from './qin-602-asset-view-harness.mjs';

const managerJs = readAssetManagerAggregate();

test('AC1: 嵌入态下剧本标签隐藏 content-heading，实现 Coverflow 与工具栏控件靠左对齐', async () => {
  // 【QIN-602 改前 → 改后】原 4 条源码正则（scripts 显式 hideHeading / QIN-574 隐藏名单 /
  // options.hideHeading ? '' : ... 模板分支 / renderRegistryToolbar 调用签名字面量）
  // 统一改为真实执行 renderRegistryToolbar() 与汇聚点后断言输出 DOM；意图与覆盖面不缩窄。
  const harness = createAssetViewHarness({embedded: true, tab: 'inbox'});
  const toolbar = (tab, options = {}) => {
    harness.setTab(tab);
    return harness.api.renderRegistryToolbar(null, `标题-${tab}`, `副标题-${tab}`, {allowUrl: true, ...options});
  };

  // ① 嵌入态 scripts 显式 hideHeading；② QIN-574 prompts/canvas-assets/workflows 契约
  for(const tab of ['scripts', 'prompts', 'canvas-assets', 'workflows']){
    assert.ok(
      !toolbar(tab).includes('class="content-heading"'),
      `嵌入态 ${tab} 必须隐藏 content-heading`,
    );
  }
  // ③ options.hideHeading ? '' : (hideHeading ? '' : ...) 两条分支都真跑
  assert.ok(toolbar('inbox').includes('class="content-heading"'), '默认工具栏应输出 content-heading');
  assert.ok(
    !toolbar('inbox', {hideHeading: true}).includes('class="content-heading"'),
    '显式 hideHeading 时工具栏必须输出空字符串',
  );
  assert.ok(toolbar('inbox').includes('data-registry-add-url'), 'allowUrl:true 应输出添加 URL 控件');

  // ④ 汇聚点调用签名契约：registry / projects 隐藏标题，收集箱 / 挂载文件夹显示；
  //    并顺带锁定 QIN-602 顶栏标题按当前看板冻结（替代原字面量断言）。
  const boards = {
    inbox: {id: 'inbox', name: '收集箱', system: true},
    folderA: {id: 'folderA', name: '挂载文件夹A', system: false},
  };
  for(const [tab, hidden] of [['registry', true], ['projects', true], ['inbox', false], ['folderA', false]]){
    const view = createAssetViewHarness({embedded: true, tab, boards});
    view.api.renderActiveAssetView();
    await view.flush();
    assert.equal(
      view.root.innerHTML.includes('class="content-heading"'),
      !hidden,
      `${tab} 工具栏 hideHeading 行为须与 QIN-572 / QIN-574 契约一致`,
    );
    assert.ok(view.root.innerHTML.includes('data-registry-add-url'), `${tab} 应保留 allowUrl:true`);
    assert.equal(view.root.countClass('registry-toolbar'), 1, `${tab} 工具栏应恰好 1 份`);
  }
});

test('AC2: 顶栏概览统计卡片顺序调整为 画布 → 其他 → 项目 → 剧本 → 提示词 → LUT', () => {
  const statsSlice = managerJs.slice(
    managerJs.indexOf('function renderRegistryOverviewStats'),
    managerJs.indexOf('function toggleRegistryFeature')
  );
  const canvas = statsSlice.indexOf("{id:'reference-canvas'");
  const other = statsSlice.indexOf("{id:'other'");
  const projects = statsSlice.indexOf("{id:'projects', name:'项目', nav:true");
  const scripts = statsSlice.indexOf("{id:'scripts', name:'剧本', nav:true");
  const prompts = statsSlice.indexOf("{id:'prompts', name:'提示词', nav:true");
  const lut = statsSlice.indexOf("{id:'lut', name:'LUT', nav:true");

  for (const pos of [canvas, other, projects, scripts, prompts, lut]) {
    assert.ok(pos > 0, '所有关键统计卡片与导航入口必须存在于 stats 数组中');
  }

  // 【QIN-580】LUT 保留导航卡但排在最后，顺序为「画布 → 其他 → 项目 → 剧本 → 提示词 → LUT」。
  assert.ok(
    canvas < other && other < projects && projects < scripts && scripts < prompts && prompts < lut,
    '卡片顺序必须为 画布 → 其他 → 项目 → 剧本 → 提示词 → LUT'
  );
  assert.doesNotMatch(statsSlice, /id:'lut', name:'LUT',[^\n]*registryCategoryCount\('lut'\)/, 'LUT 不得出统计数值卡');
});

test('AC3: 顶栏导航卡片保留 is-nav 与 data-registry-nav，点击激活对应系统看板', () => {
  assert.match(managerJs, /data-registry-nav=/);
  assert.match(managerJs, /is-nav/);
  const navClickBlock = managerJs.slice(
    managerJs.indexOf("data-registry-nav]')){"),
    managerJs.indexOf('const registryStat =')
  );
  assert.match(navClickBlock, /gwCall\('organize', 'activateAssetBoard', navBoard\)/);
  assert.match(navClickBlock, /saveAssetTreeState\(\)/);
});
