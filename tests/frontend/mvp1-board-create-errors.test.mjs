// QIN-485: 资产库新增资源库弹窗错误回显与交互状态契约测试
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source = readAssetManagerAggregate();
const organize = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const shell = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');

test('新建资源入口移入左栏页头（QIN-566 恢复外壳侧栏）并沿用独立标准硬件按钮', () => {
    assert.match(shell, /id="assetSidebarCreate" class="hw-btn hw-btn-icon" onclick="WorkbenchAssets\.openCreateAssetLibrary\(\)"/);
    assert.match(shell, /id="assetVaultSidebar"/);
    assert.match(shell, /id="assetCategories"/);
    assert.match(controller, /async function openCreateAssetLibrary\(\)[\s\S]*?button\.click\(\)/);
    assert.doesNotMatch(controller, /asset-shell-nav-create/);
});

test('新增资源库弹窗已移除并统一收敛到「+ 添加资源文件夹」标准弹窗（QIN-599 AC2）', () => {
    assert.doesNotMatch(source, /function renderCreateLibraryModal|function openCreateLibraryModal|function createPresetAssetBoard/);
    assert.doesNotMatch(source, /board-preset-grid|data-board-preset/);
    assert.match(source, /data-board-create[^>]*aria-label="\+ 添加资源文件夹"[^>]*>[\s\S]*?\+ 添加资源文件夹/);
    assert.match(source, /gwCall\('organize', 'openBoardSourceModal', '', 'new'\)/);
    assert.match(source, /<strong id="boardSourceTitle">添加资源文件夹<\/strong>/);
    assert.match(styles, /\.board-source-modal\s*\{[^}]*width:min\(620px, 94vw\)/);
    assert.match(styles, /\.board-create-form\s*\{[^}]*grid-template-columns:minmax\(0,1fr\)/);
    assert.doesNotMatch(styles, /board-preset-grid|board-preset-card/);
});

test('添加资源文件夹取消旧版 boardCreatePath 表单，统一走资源文件夹来源弹窗与目录输入（QIN-599 AC2）', () => {
    assert.doesNotMatch(source, /BOARD_CREATE_PATH_FIELD_KEY|function updateBoardCreatePath/);
    assert.match(source, /<label><span>来源名称<\/span><input id="boardSourceName"/);
    assert.match(organize, /<label class="board-source-path-label"><span>素材路径<\/span><div class="board-source-path-row"><input id="boardSourcePath"[\s\S]*?data-board-source-pick/);
});

test('新建资源库折叠右侧详情栏后释放网格宽度，不保留空白列', () => {
    assert.match(source, /function syncRegistryDetailGridWidth\(\)\{\s*root\?\.style\.setProperty\('--registry-detail-width',`\$\{registryDetailPanelCollapsed \? 44 : registryDetailPanelWidth\}px`\);\s*\}/);
    assert.match(source, /root\.classList\.toggle\('registry-detail-collapsed'[\s\S]*?syncRegistryDetailGridWidth\(\);/);
    assert.match(source, /root\.className = `asset-manager-root[\s\S]*?syncRegistryDetailGridWidth\(\);/);
});

test('管理员添加资源文件夹时将未登记路径加入允许根，非管理员沿用现有权限校验', () => {
    // QIN-596 / QIN-599：ensure 入口保留拒绝清单与管理员自动登记；统一从 attachPathToAssetBoard('', name, path) 触发。
    assert.match(settings, /async function ensureCreateLibraryPathAllowedRoot\(path\)\{[\s\S]{0,600}?await loadRegistryPrincipal\(\);\s*if\(!canManageAllowedRoots\(\)\) return true;/);
    assert.match(settings, /body:JSON\.stringify\(\{path, expected_version:Number\(snapshot\.revision \|\| 1\)\}\)/);
    assert.match(organize, /const allowed = options\.atomicRegistration\s*\?\s*true\s*:\s*await ensureCreateLibraryPathAllowedRoot\(cleanPath\);\s*if\(!allowed\) return null;/);
    assert.match(organize, /register_root:\{[^}]*authorize_root:Boolean\(options\.atomicRegistration\)/);
    assert.match(organize, /source = await attachPathToAssetBoard\('', name, path, options\)/);
});

test('添加资源文件夹命中已移除旧根时确认恢复并继续添加', () => {
    assert.match(source, /function confirmRestoreRemovedAllowedRoot\(root\)[\s\S]*?旧根已移除，是否恢复？[\s\S]*?data-root-restore-confirm[^>]*>恢复并继续/);
    assert.match(source, /data-root-restore-cancel/);
    assert.match(source, /surface:'asset-root-restore-confirm'[\s\S]*?close:\(\) => close\(false\)/);
    assert.match(source, /filter\(root => root\.active === false && assetPathWithinAllowedRoot\(path, root\.path\)\)[\s\S]*?sort\(\(left, right\) => pathCompareKey\(right\.path\)\.length - pathCompareKey\(left\.path\)\.length\)/);
    assert.match(source, /`\/api\/storage-settings\/roots\/\$\{encodeURIComponent\(removedRoot\.id\)\}\/restore`[\s\S]*?body:JSON\.stringify\(\{expected_version:Number\(snapshot\.revision \|\| 1\)\}\)/);
    assert.match(source, /if\(!await confirmRestoreRemovedAllowedRoot\(removedRoot\)\) return false;/);
    // QIN-596：恢复确认在 ensure 内完成；取消 → attach 返回 null（无写盘）。
    assert.match(source, /if\(!allowed\) return null;/);
});

test('旧根确认后只恢复最具体祖先，取消不写入允许根', async () => {
    const pathHelperStart = source.indexOf('function assetPathWithinAllowedRoot(');
    const pathHelperEnd = source.indexOf('\nfunction confirmRestoreRemovedAllowedRoot(', pathHelperStart);
    // QIN-596：ensure 入口新增拒绝清单——抽测注入需包含清单常量与纯函数。
    const listStart = source.indexOf('const RESOURCE_FOLDER_BLOCKED_WIN_SEGMENTS');
    const ensureStart = source.indexOf('async function ensureCreateLibraryPathAllowedRoot(');
    const ensureEnd = source.indexOf('\n// 用户显式指定缓存目录', ensureStart);
    assert.ok(pathHelperStart >= 0 && pathHelperEnd > pathHelperStart && listStart >= 0 && ensureStart > listStart && ensureEnd > ensureStart);
    const pathHelper = source.slice(pathHelperStart, pathHelperEnd);
    const listHelper = source.slice(listStart, ensureStart);
    const ensureHelper = source.slice(ensureStart, ensureEnd);
    const simulate = async confirmed => {
        const calls = [];
        const snapshot = {
            revision:7,
            roots:[
                {id:'removed-parent', path:'D:\\Vault', active:false},
                {id:'removed-child', path:'D:\\Vault\\assets', active:false},
            ],
        };
        const context = {
            pathCompareKey:value => String(value || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase(),
            loadRegistryPrincipal:async () => {},
            canManageAllowedRoots:() => true,
            confirmRestoreRemovedAllowedRoot:async () => confirmed,
            applyAllowedRootSettingsData:() => {},
            assetManagerHttp:{request:(url, options={}) => ({url, ...options})},
            apiJsonResponse:async request => {
                calls.push(request);
                if(request.method === 'GET') return snapshot;
                if(request.url.endsWith('/restore')) return {...snapshot, revision:8, roots:snapshot.roots.map(root => ({...root, active:root.id === 'removed-child'}))};
                throw new Error(`unexpected request ${request.method} ${request.url}`);
            },
        };
        const result = await runInNewContext(`${pathHelper}\n${listHelper}\n${ensureHelper}\nensureCreateLibraryPathAllowedRoot('D:/Vault/assets/new')`, context);
        return {result, calls};
    };

    const accepted = await simulate(true);
    assert.equal(accepted.result, true);
    const restoreCall = accepted.calls.find(call => call.url.endsWith('/restore'));
    assert.match(restoreCall.url, /removed-child\/restore$/);
    assert.deepEqual(JSON.parse(restoreCall.body), {expected_version:7});
    assert.equal(accepted.calls.filter(call => call.url === '/api/storage-settings/roots' && call.method === 'POST').length, 0);

    const cancelled = await simulate(false);
    assert.equal(cancelled.result, false);
    assert.equal(cancelled.calls.some(call => call.url.endsWith('/restore') || call.method === 'POST'), false);
});

test('资源文件夹弹窗模板包含错误回显容器与状态图标', () => {
    assert.match(source, /boardSourceState\.error\s*\n\s*\?\s*`<div class="board-create-error" role="alert">/);
    assert.match(source, /data-lucide="alert-circle"/);
    assert.match(source, /escapeHtml\(boardSourceState\.error\)/);
});

test('资源文件夹确认与关闭按钮在 saving 状态下禁用防重并展示 loading', () => {
    assert.match(organize, /data-board-source-editor-cancel \$\{boardSourceState\.saving \? 'disabled' : ''\}/);
    assert.match(organize, /data-board-source-confirm \$\{\(boardSourceState\.saving \|\| boardSourceState\.picking \|\| boardSourceState\.validating\) \? 'disabled' : ''\}/);
    assert.match(organize, /boardSourceState\.saving \|\| boardSourceState\.picking \|\| boardSourceState\.validating\) \? 'loader-2' : 'folder-plus'/);
    assert.match(organize, /boardSourceState\.picking \? '正在选择…' : boardSourceState\.validating \? '正在检查…' : boardSourceState\.saving \? '正在添加…' : '添加来源'/);
});

test('confirmBoardSource 捕获异常后将错误同步到 boardSourceState.error 并重新渲染弹窗', () => {
    assert.match(organize, /boardSourceState\.error = err\?\.code === 'RESOURCE_FOLDER_BACKEND_UNAVAILABLE'[\s\S]*?: \(err\?\.message \|\| '添加资源文件夹失败'\);/);
    assert.match(organize, /if\(boardSourceState\.open\) renderBoardSourceModal\(\);/);
});

test('attachPathToAssetBoard 对别名建议查询具备容错保护', () => {
    assert.match(source, /suggestionData = await apiJsonResponse\(assetManagerApi\.getRootAliasSuggestions\(\{path:cleanPath\}\)\);\s*\} catch\(_\) \{\s*suggestionData = null;/);
});

test('asset-manager.css 中包含 .board-create-error 样式规则', () => {
    assert.match(styles, /\.board-create-error\s*\{[^}]*color:\s*#ef4444/);
    assert.match(styles, /\.board-create-error\s*svg\s*\{[^}]*stroke:\s*#ef4444/);
});

test('资源文件夹追加不生成自定义看板，直接刷新分面与目录并异步跟踪索引 (QIN-599 AC2)', () => {
    assert.doesNotMatch(source, /state\.customBoards\.push/);
    assert.doesNotMatch(source, /createPresetAssetBoard/);
    assert.match(source, /notifyShellTreeSnapshot\(\);\s*\n\s*render\(\);/);
    assert.match(source, /waitForAssetIndexJob\(source\.job_id, source\.name\)\.catch\(\(\) => \{\}\);/);
});

test('waitForAssetIndexJob 超时友好提示且不抛出阻断异常 (QIN-487)', () => {
    assert.match(source, /setStatus\(`素材库“\$\{sourceName\}”索引仍在后台运行，可稍后刷新查看`\);\s*return null;/);
    assert.doesNotMatch(source, /throw new Error\(`素材库“\$\{sourceName\}”索引仍在后台运行/);
});

test('attachPathToAssetBoard 返回带有 job_id 的 source 且不阻塞等待 (QIN-487)', () => {
    assert.match(source, /if\(accepted\?\.job_id\)\{\s*source\.job_id = accepted\.job_id;\s*\}/);
    assert.doesNotMatch(source, /await waitForAssetIndexJob\(accepted\?\.job_id/);
});
