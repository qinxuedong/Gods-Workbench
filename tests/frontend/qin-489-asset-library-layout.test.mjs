import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const shell = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');

function functionSource(name){
    const start = manager.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `缺少函数：${name}`);
    const open = manager.indexOf('{', start);
    let depth = 0;
    for(let index = open; index < manager.length; index += 1){
        if(manager[index] === '{') depth += 1;
        else if(manager[index] === '}'){
            depth -= 1;
            if(depth === 0) return manager.slice(start, index + 1);
        }
    }
    assert.fail(`函数未闭合：${name}`);
}

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
    assert.ok(manager.includes("${embeddedVault ? '' : renderRegistryOverview()}"), '独立资产管理页仍显示统计区');
    assert.ok(manager.includes("${embeddedVault ? '' : renderRegistryToolbar(null, registryCategoryLabel"), '独立资产总览仍显示工具栏');
    assert.ok(manager.includes("${embeddedVault ? '' : renderRegistryToolbar(board, board.name"), '独立自建资源库仍显示工具栏');
    assert.match(vaultCss, /#assetManagerRoot > \.registry-overview-shell \{[\s\S]*grid-row: 1;/);
    assert.match(vaultCss, /#assetManagerRoot > \.registry-toolbar \{[\s\S]*grid-row: 2;/);
    assert.match(vaultCss, /#assetManagerRoot > \.asset-panel\.asset-content \{[\s\S]*grid-row: 3;/);
    for (const id of ['registry', 'scripts', 'projects', 'assets', 'workflows', 'prompts', 'canvas-assets', 'local', 'governance']) {
        assert.ok(manager.includes(`id:'${id}'`), `资源库缺少入口：${id}`);
    }
});

test('QIN-504 AC1: 虚拟网格渲染后重新绑定滚动窗口', () => {
    const renderStart = manager.indexOf('function render(){');
    const renderEnd = manager.indexOf('\nfunction updateSearchQueryFromInput', renderStart);
    assert.ok(renderStart >= 0 && renderEnd > renderStart);
    const render = manager.slice(renderStart, renderEnd);
    assert.ok(render.indexOf('scheduleRegistryVirtualWindow();') > render.indexOf('scheduleRegistryMasonryLayout();'));
});

test('QIN-504 AC2: 虚拟卡片列数沿用实际 CSS 网格列数', () => {
    const columnSource = functionSource('registryGridColumnCount');
    const layoutSource = functionSource('registryVirtualLayout');
    const grid = {clientWidth:1750};
    let template = '241.42px 241.42px 241.42px 241.42px 241.42px 241.42px 241.42px';
    const buildLayout = new Function(
        'root','getComputedStyle','REGISTRY_VIRTUAL_GAP','REGISTRY_CARD_SIZE','registryCardSize','registrySimple','registryAspect','registryVirtualCardHeight',
        `${columnSource}\n${layoutSource}\nreturn registryVirtualLayout;`,
    );
    const layout = buildLayout(
        {querySelector:selector => selector === '.registry-grid' ? grid : null},
        () => ({gridTemplateColumns:template}),
        10,
        {min:96,max:480,fallback:176},
        176,
        false,
        'square',
        () => 300,
    );
    const regular = layout([{id:'a'},{id:'b'}]);
    assert.equal(regular.cols, 7);
    assert.ok(Math.abs(regular.cardWidth - (1750 - 60) / 7) < 0.01);
    template = 'repeat(7, minmax(0px, 1fr))';
    assert.equal(layout([{id:'a'},{id:'b'}]).cols, 7);
});

test('QIN-504 AC3: 快速分页后同步资产库计数与续载状态', () => {
    const footer = functionSource('syncRegistryVaultFooter');
    const loadStart = manager.indexOf('async function loadMoreRegistryAssets(){');
    const loadEnd = manager.indexOf('// 【浏览预加载】契约', loadStart);
    assert.ok(loadStart >= 0 && loadEnd > loadStart);
    assert.match(footer, /已加载 \$\{registryAssets\.length\} 项/);
    assert.match(footer, /registryPageHasMore \? '下滑加载更多'/);
    assert.match(manager.slice(loadStart, loadEnd), /syncRegistryVaultFooter\(\);/);
});
