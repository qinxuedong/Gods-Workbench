import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const shell = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');

test('AC1: 资源库左侧导航承载资产看板与新建资源入口', () => {
    assert.match(html, /<span>资源库<\/span>/);
    assert.match(html, /id="assetCategories" role="tablist" aria-label="资源库导航"/);
    assert.match(shell, /querySelectorAll\('#assetBoardTabs \.asset-board-tab'\)/);
    assert.match(shell, /data-asset-board-create/);
    for (const label of ['资产总览', '剧本资产', '项目制片', '托管资产', '工作流', '提示词', '画布资产', '上传资源', '治理建议']) {
        assert.ok(manager.includes(`name:'${label}'`), `缺少导航项：${label}`);
    }
    assert.match(shell, /label\.textContent = '新建资源'/);
    assert.match(vaultCss, /html\.vault-shell-embedded[^\n]*#assetBoardTabs \{ display: none !important; \}/);
});

test('AC2: 状态显示在固定顶栏并从嵌入资产管理器回写', () => {
    const statusIndex = html.indexOf('ASSET VAULT');
    const statusNodeIndex = html.indexOf('id="assetVaultStatus"');
    const sidebarIndex = html.indexOf('id="assetVaultSidebar"');
    assert.ok(statusIndex >= 0 && statusNodeIndex > statusIndex && statusNodeIndex < sidebarIndex);
    assert.doesNotMatch(html.slice(html.indexOf('id="navPillsGroup"'), sidebarIndex), /assetVaultStatus/);
    assert.match(manager, /window\.parent\.document\.getElementById\('assetVaultStatus'\)/);
    assert.match(manager, /embeddedVault \? '' : `<div id="assetStatus"/);
});

test('AC3: 智能筛选与资产设置入口固定在左栏底部并转发到现有动作', () => {
    assert.match(html, /id="assetSidebarSmartFilter"[^>]*onclick="WorkbenchAssets\.toggleSmartFilter\(\)"/);
    assert.match(html, /id="assetSidebarSettings"[^>]*onclick="WorkbenchAssets\.openAssetSettings\(\)"/);
    assert.match(html, /class="asset-shell-sidebar-actions[^]*?id="assetSidebarSmartFilter"[^]*?id="assetSidebarSettings"/);
    assert.match(shell, /function toggleSmartFilter\(\)[\s\S]*?panel\.showPopover\(\)[\s\S]*?asset-registry-filters-collapsed/);
    assert.match(shell, /async function openAssetSettings\(\)/);
    assert.match(shell, /#assetBoardTabs \[data-board-settings-open\]/);
    assert.match(vaultCss, /registry-toolbar \.vault-filters-toggle \{ display: none !important; }/);
    assert.match(vaultCss, /asset-manager-root-registry\.asset-registry-filters-collapsed/);
});

test('AC4: 刷新按钮位于搜索前，搜索宽度限制为可用栏约一半', () => {
    const toolbarStart = manager.indexOf('function renderRegistryToolbar(');
    const refreshIndex = manager.indexOf('data-refresh-assets', toolbarStart);
    const searchIndex = manager.indexOf('id="registrySearch"', toolbarStart);
    assert.ok(toolbarStart >= 0 && refreshIndex > toolbarStart && searchIndex > refreshIndex);
    assert.match(vaultCss, /registry-refresh \{ order: -2/);
    assert.match(vaultCss, /asset-search-wrap \{ order: -1; flex: 0 1 calc\(50% - 44px\); max-width: calc\(50% - 44px\)/);
    assert.match(vaultCss, /flex-basis: calc\(50% - 52px\)/);
});

test('AC5: 窄屏时左侧导航转为可横向滚动入口', () => {
    assert.match(html, /@media \(max-width: 700px\)[\s\S]*?#assetCategories \{[^}]*overflow-x: auto/);
    assert.doesNotMatch(html, /@media \(max-width: 700px\) \{ #assetVaultSidebar \{ display: none/);
});

test('AC7: 左栏全部入口共用固定统计行和工具栏行，视图留在资源库页内切换', () => {
    assert.match(manager, /if\(document\.documentElement\.classList\.contains\('vault-shell-embedded'\)\)[\s\S]*root\.insertAdjacentHTML\('afterbegin', `\$\{renderRegistryOverview\(\)\}\$\{renderRegistryToolbar\(null,/);
    assert.match(vaultCss, /#assetManagerRoot > \.registry-overview-shell \{[\s\S]*grid-row: 1;/);
    assert.match(vaultCss, /#assetManagerRoot > \.registry-toolbar \{[\s\S]*grid-row: 2;/);
    assert.match(vaultCss, /#assetManagerRoot > \.asset-panel\.asset-content \{[\s\S]*grid-row: 3;/);
    for (const id of ['registry', 'scripts', 'projects', 'assets', 'workflows', 'prompts', 'canvas-assets', 'local', 'governance']) {
        assert.ok(manager.includes(`id:'${id}'`), `资源库缺少入口：${id}`);
    }
});
