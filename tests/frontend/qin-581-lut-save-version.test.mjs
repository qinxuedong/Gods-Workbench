import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const read = relative => readFileSync(new URL(`../../${relative}`, import.meta.url), 'utf8');
const manager = readAssetManagerAggregate();
const styles = read('web/css/pages/asset-manager.css');
const routes = read('gw/api/routes_asset_registry.py');

/** QIN-581 保存段：从段首注释到 QIN-554 工作区函数之前。 */
const saveSection = (() => {
    const start = manager.indexOf('QIN-581：把套用结果保存为新的资产版本');
    assert.ok(start > 0, '找不到 QIN-581 保存代码段');
    const end = manager.indexOf('function setDetailLutPreviewError', start);
    assert.ok(end > start, '找不到 QIN-581 代码段结束位置');
    return manager.slice(start, end);
})();

/** QIN-580 预览段：保存写路径不得混进来，预览段必须保持只读。 */
const previewSection = (() => {
    const start = manager.indexOf('QIN-580：参考画布素材套用 LUT 预览');
    assert.ok(start > 0, '找不到 QIN-580 预览代码段');
    const end = manager.indexOf('function renderDetailLutWorkspace(source, item){', start);
    assert.ok(end > start, '找不到 QIN-580 代码段结束位置');
    return manager.slice(start, end);
})();

test('QIN-581：节点 LUT 浮窗提供「保存为版本」入口', () => {
    assert.ok(
        previewSection.includes('data-detail-media-node-lut-save'),
        '浮窗内必须提供保存入口挂点',
    );
    assert.ok(previewSection.includes('保存为版本'), '入口文案应为「保存为版本」');
    // 入口必须排在「清除 LUT」旁边，用户套完就能直接保存。
    const saveIndex = previewSection.indexOf('data-detail-media-node-lut-save');
    const clearIndex = previewSection.indexOf('data-detail-media-node-lut-clear');
    assert.ok(saveIndex > 0 && clearIndex > 0, '找不到保存/清除入口');
    assert.ok(saveIndex < clearIndex, '保存入口应排在清除之前');
});

test('QIN-581：保存请求打到后端真实端点，参数与路由一致', () => {
    // 前端模板：/api/asset-registry/assets/<id>/lut-renders
    assert.ok(
        saveSection.includes(
            '/api/asset-registry/assets/${encodeURIComponent(key)}/lut-renders',
        ),
        '保存必须调用 LUT 渲染入库端点',
    );
    assert.match(saveSection, /method:'POST'/, '保存必须是写请求');
    assert.match(saveSection, /credentials:'same-origin'/, '保存请求需带同源凭据');
    assert.match(saveSection, /'Content-Type':'application\/json'/, '保存需声明 JSON 请求体');
    // 请求体必须显式给出 LUT 资产与强度，强度取当前节点实际值而不是常量。
    assert.match(saveSection, /lut_asset_id:lutId/, '请求体缺少 lut_asset_id');
    assert.match(
        saveSection,
        /strength:detailMediaLutStrengthOf\(entry\)/,
        '强度必须取当前节点值，不能硬编码',
    );
    // 后端路由必须存在同名端点，且挂在 asset_id 路径参数下。
    assert.match(
        routes,
        /@router\.post\("\/assets\/\{asset_id\}\/lut-renders"/,
        '后端缺少 /assets/{asset_id}/lut-renders 端点',
    );
});

test('QIN-581：重复点击与保存中状态都被拦住', () => {
    assert.match(saveSection, /if\(button\?\.disabled\) return;/, '保存中不得重复提交');
    assert.match(saveSection, /button\.disabled = true/, '提交期间必须禁用按钮');
    assert.match(saveSection, /button\.dataset\.state = 'busy'/, '提交期间应有可见状态');
    assert.match(saveSection, /保存中…/, '提交期间应有文案反馈');
    // 结束态无论成功失败都要恢复按钮，否则按钮会永久卡在禁用。
    const finallyBlock = saveSection.slice(saveSection.indexOf('} finally {'));
    assert.ok(finallyBlock.length > 0, '保存流程必须有 finally 恢复');
    assert.match(finallyBlock, /button\.disabled = false/, 'finally 必须恢复按钮');
    assert.match(finallyBlock, /label\.textContent = labelText/, 'finally 必须恢复文案');
});

test('QIN-581：成功与失败都有明确反馈，失败带稳定错误码', () => {
    assert.match(saveSection, /showAssetNotice\(`已保存为新资产/, '成功需提示新资产名');
    assert.match(saveSection, /原素材未改动/, '提示应说明原素材未被覆盖');
    const failure = saveSection.slice(saveSection.indexOf('} catch(error){'));
    assert.ok(failure.length > 0, '保存流程必须有失败分支');
    assert.match(failure, /showAssetNotice\(/, '失败需可见提示');
    assert.match(failure, /error\?\.code/, '失败提示需带上后端错误码');
    assert.match(failure, /'error'/, '失败提示应为错误态');
});

test('QIN-581：保存后不打断正在看的预览', () => {
    // 只把新资产并入内存列表，不触发整树重绘或全量刷新。
    for (const forbidden of ['render();', 'refreshRegistryAssets(', 'loadRegistryBootstrap(']) {
        assert.ok(!saveSection.includes(forbidden), `保存路径不应触发 ${forbidden}`);
    }
    // QIN-591：registryAssets 为跨文件写状态，lut.js 内赋值改写为 setter 调用（登记在案），并入语义不变。
    assert.match(saveSection, /setRegistryAssets\(\[normalized, \.\.\.registryAssets\]\)/, '新资产应并入列表数据');
});

test('QIN-581：点击分支挂在节点浮窗内，且排在「点空白收起」之前', () => {
    const saveBranch = manager.indexOf("closest?.('[data-detail-media-node-lut-save]')");
    const clearBranch = manager.indexOf("closest?.('[data-detail-media-node-lut-clear]')");
    const dismiss = manager.indexOf("if(String(detailViewerState.mediaCanvasLutPanel || '')){ closeDetailMediaNodeLutPanel(); }");
    assert.ok(saveBranch > 0, '缺少保存点击分支');
    assert.ok(clearBranch > 0 && dismiss > 0, '找不到对照分支');
    assert.ok(saveBranch < clearBranch, '保存分支应紧邻清除分支');
    assert.ok(saveBranch < dismiss, '保存分支必须排在「点空白收起」兜底之前，否则点不到');
    // 分支要用的目标 id 来自浮窗容器，不能复用角标节点。
    const panelBranch = manager.slice(
        manager.indexOf('const mediaNodeLutPanel = event.target.closest'),
        saveBranch,
    );
    assert.ok(panelBranch.includes('targetId'), '保存分支应复用浮窗的 targetId');
});

test('QIN-581：样式挂点齐备且字号走令牌', () => {
    for (const selector of [
        '.asset-detail-media-node-lut-save',
        '.asset-detail-media-node-lut-save:hover',
        '.asset-detail-media-node-lut-save[disabled]',
    ]) {
        assert.ok(styles.includes(selector), `缺少样式 ${selector}`);
    }
    const block = styles.slice(
        styles.indexOf('.asset-detail-media-node-lut-save {'),
        styles.indexOf('.asset-detail-media-node-lut-save svg'),
    );
    assert.ok(block.length > 0, '找不到保存按钮样式块');
    // typography governance：字号必须引用令牌，不得裸写 px。
    assert.ok(!/font-size:\s*\d+px/.test(block), '保存按钮字号必须使用 --gw-type-* 令牌');
    assert.match(block, /font-size:\s*var\(--gw-type-/, '保存按钮缺少字号令牌');
});
