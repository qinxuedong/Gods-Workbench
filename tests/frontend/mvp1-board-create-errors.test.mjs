// QIN-485: 资产库新增资源库弹窗错误回显与交互状态契约测试
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const shell = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');

test('新建资源入口位于侧栏资源库列表上方并沿用标准硬件按钮', () => {
    assert.match(shell, /<button type="button" id="assetSidebarCreate" class="hw-btn w-full[^\"]*" onclick="WorkbenchAssets\.openCreateAssetLibrary\(\)"/);
    assert.match(shell, /id="assetSidebarCreate"[\s\S]*?<\/button>[\s\S]*?<div class="gw-side-bus-list[^\"]*" id="assetCategories"/);
    assert.match(controller, /async function openCreateAssetLibrary\(\)[\s\S]*?button\.click\(\)/);
    assert.doesNotMatch(controller, /asset-shell-nav-create/);
});

test('新增资源库弹窗取消类型预设，只按名称、路径两行填写并使用紧凑居中标准卡片', () => {
    assert.doesNotMatch(source, /board-preset-grid|data-board-preset/);
    assert.match(source, /\{id:'other', name:'其他资源'[\s\S]*?category:'', kinds:\[\], extensions:\[\]\}/);
    assert.match(source, /board-create-form board-create-form-path/);
    assert.match(source, /<label><span>资源库名称<\/span><input id="boardCreateName"/);
    assert.match(source, /<label class="board-create-path-field"><span>资源路径（可选）<\/span><div class="directory-input-row"><input id="boardCreatePath"/);
    assert.match(styles, /\.board-create-modal\s*\{[^}]*width:min\(620px, 94vw\)/);
    assert.match(styles, /\.board-create-form\s*\{[^}]*grid-template-columns:minmax\(0,1fr\)/);
    assert.doesNotMatch(styles, /board-preset-grid|board-preset-card/);
    assert.match(source, /board\.preset === 'other'[\s\S]*?暂无可见资源/);
});

test('资源路径支持手输及右侧目录选择，选择目录只回填表单', () => {
    assert.match(source, /class="hw-btn hw-btn-icon directory-picker-btn" data-directory-picker="\$\{BOARD_CREATE_PATH_FIELD_KEY\}"/);
    assert.match(source, /async function fillServerDirectoryFromNative\(key, pathValue\)\{\s*if\(key === BOARD_CREATE_PATH_FIELD_KEY\)\{\s*boardCreateState\.path = String\(pathValue \|\| ''\)\.trim\(\);/);
    assert.match(source, /createPathSelection \? '选择此目录' : '选择并挂载'/);
    assert.match(source, /createPathSelection \? '选择当前目录' : '选择当前目录并挂载'/);
    assert.match(source, /仅显示服务器文件夹；选择后返回创建表单，点击创建时再登记路径/);
});

test('管理员创建时将未登记路径加入允许根，非管理员沿用现有权限校验', () => {
    assert.match(source, /async function ensureCreateLibraryPathAllowedRoot\(path\)\{\s*await loadRegistryPrincipal\(\);\s*if\(!canManageAllowedRoots\(\)\) return;/);
    assert.match(source, /body:JSON\.stringify\(\{path, expected_version:Number\(snapshot\.revision \|\| 1\)\}\)/);
    assert.match(source, /await ensureCreateLibraryPathAllowedRoot\(path\);\s*source = await attachPathToAssetBoard\('', `\$\{name\}素材`, path\)/);
});

test('新增资源库弹窗模板包含错误回显容器与状态图标', () => {
    assert.match(source, /boardCreateState\.error \? `<div class="board-create-error" role="alert">/);
    assert.match(source, /data-lucide="alert-triangle"/);
    assert.match(source, /escapeHtml\(boardCreateState\.error\)/);
});

test('新增资源库确认与取消按钮在 saving 状态下禁用防重并展示 loading', () => {
    assert.match(source, /data-board-create-close \$\{boardCreateState\.saving \? 'disabled' : ''\}/);
    assert.match(source, /data-board-create-confirm \$\{boardCreateState\.saving \? 'disabled' : ''\}/);
    assert.match(source, /boardCreateState\.saving \? 'loader' : 'plus'/);
    assert.match(source, /boardCreateState\.saving \? '正在创建…' : '创建资源库'/);
});

test('createPresetAssetBoard 捕获异常后将错误同步到 boardCreateState.error 并重新渲染弹窗', () => {
    assert.match(source, /boardCreateState\.saving = false;\s*boardCreateState\.error = err\.message/);
    assert.match(source, /renderCreateLibraryModal\(\);\s*markIndexState\('failed'\)/);
});

test('attachPathToAssetBoard 对别名建议查询具备容错保护', () => {
    assert.match(source, /suggestionData = await apiJsonResponse\(assetManagerApi\.getRootAliasSuggestions\(\{path:cleanPath\}\)\);\s*\} catch\(_\) \{\s*suggestionData = null;/);
});

test('asset-manager.css 中包含 .board-create-error 样式规则', () => {
    assert.match(styles, /\.board-create-error\s*\{[^}]*color:\s*#ef4444/);
    assert.match(styles, /\.board-create-error\s*svg\s*\{[^}]*stroke:\s*#ef4444/);
});

test('新资源库追加到列表下方并自动激活，来源索引异步跟踪 (QIN-487 / QIN-501)', () => {
    assert.match(source, /ASSET_LIBRARY_PRESETS\.find\(item => item\.id === 'other'\)/);
    assert.match(source, /state\.customBoards\.push\(\{id, name, preset:preset\.id/);
    assert.match(source, /state\.order\.push\(id\);\s*if\(source\) boardSourceSelection\[id\] = source\.id;\s*saveBoardNavigationState\(\);\s*closeCreateLibraryModal\(\);\s*renderBoardNavigation\(\);/);
    assert.match(source, /await activateAssetBoard\(id\);/);
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
