// QIN-572: 资产库项目标签清除中间空栏、重置筛选并支持项目下拉选择
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const managerJs = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');

test('AC1: vault 嵌入态项目视图网格首列收为零宽，消除 240px 空白列', () => {
  // 必须存在针对 .asset-manager-root-page-projects 的零宽规则
  assert.match(vaultCss, /html\.vault-shell-embedded body\.gw-signal\.gw-page--asset-manager #assetManagerRoot\.asset-manager-root-page-projects \{\s*\/\* 【QIN-572】[^]*?\*\/\s*grid-template-columns: 0 minmax\(0,1fr\) !important;/);
  // 三列兜底选择器必须排除 .asset-manager-root-page-projects
  assert.match(vaultCss, /#assetManagerRoot:not\(\.asset-manager-root-registry\):not\(\.asset-manager-root-library\):not\(\.asset-manager-root-page-scripts\):not\(\.asset-manager-root-page-projects\)/);
});

test('AC2: 激活项目看板时自动重置前置分类（如 LUT）与搜索/标签筛选', () => {
  assert.match(managerJs, /if\(id === 'projects'\)\s*\{[^]*?registryStatCategory = '';[^]*?registryKind = '';[^]*?registryQuery = '';/);
  // 退出项目视图时若为 __all__ 自动清空 project_id 筛选，不污染其它看板
  assert.match(managerJs, /if\(id !== 'projects' && registryProjectFilterId === '__all__'\)\s*\{\s*registryProjectFilterId = '';\s*\}/);
});

test('AC3: registryQueryString 在项目视图下携带 project_id=__all__ 或具体 project_id', () => {
  assert.match(managerJs, /if\(activeTab === 'projects' && !registryProjectFilterId\)\s*registryProjectFilterId = '__all__';\s*if\(registryProjectFilterId\)\s*params\.set\('project_id',\s*registryProjectFilterId\);/);
});

test('AC4: 工具栏包含项目选择下拉胶囊，支持全部项目与具体项目切换', () => {
  // registrySelectOptions 支持 project 类型
  assert.match(managerJs, /if\(kind === 'project'\)\s*\{\s*return \[\s*\{value:'__all__', label:'全部项目'\},/);
  // applyRegistrySelectValue 支持 project 切换
  assert.match(managerJs, /if\(kind === 'project'\)\s*\{[^]*?registryProjectFilterId = next;[^]*?refreshRegistryAssets\(false\)/);
  // renderRegistryToolbar 在 activeTab === 'projects' 时渲染项目选择胶囊
  assert.match(managerJs, /\$\{activeTab === 'projects' \? renderRegistrySelectCapsule\(\{kind:'project', icon:'folder-kanban', label:'项目'\}\) : ''\}/);
});
