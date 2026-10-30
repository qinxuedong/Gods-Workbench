// QIN-485: 资产库新增资源库弹窗错误回显与交互状态契约测试
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

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

test('createPresetAssetBoard 成功获取来源后立即创建看板并异步非阻塞跟踪索引 (QIN-487)', () => {
    assert.match(source, /state\.customBoards\.push\(\{id, name, preset:preset\.id/);
    assert.match(source, /saveBoardNavigationState\(\);\s*closeCreateLibraryModal\(\);\s*renderBoardNavigation\(\);/);
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
