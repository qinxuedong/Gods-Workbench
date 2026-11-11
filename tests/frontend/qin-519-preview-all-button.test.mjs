/**
 * QIN-519 基线 + QIN-525 交互更新：预览缓存「全部缓存」入口与全量后台任务。
 *
 * 本文件保留 QIN-519 的核心护栏（显式触发、异步任务、进度与幂等计数），
 * 结构断言按 QIN-525 合并后的单区块 / 两按钮形态更新；新增结构断言见
 * qin-525-preview-cache-merge.test.mjs。
 */
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const MANAGER = readAssetManagerAggregate();

function functionSource(name){
    const start = MANAGER.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `缺少函数：${name}`);
    const open = MANAGER.indexOf('{', start);
    let depth = 0;
    for(let index = open; index < MANAGER.length; index += 1){
        if(MANAGER[index] === '{') depth += 1;
        else if(MANAGER[index] === '}'){
            depth -= 1;
            if(depth === 0) return MANAGER.slice(start, index + 1);
        }
    }
    assert.fail(`函数未闭合：${name}`);
}

test('AC2: 预览缓存区块挂载全库入口与离线预热说明', () => {
    const body = functionSource('renderAssetPreviewSettingsBody');
    assert.match(body, /data-preview-all-start/, '入口按钮必须带 data-preview-all-start');
    assert.match(body, /为全库素材后台生成预览图，适合离线前预热/, '必须给出离线预热说明');
    // 全库口径来自后端缓存统计（settings.cache），不再用前端 facets 猜数。
    assert.match(body, /previewCacheStats\(\)/, '总数与已缓存数必须取自缓存统计');
    assert.doesNotMatch(body, /当前视图 \$\{ids\.length\} 项/, '合并后不再有「当前视图 N 项」口径');
});

test('AC3: 两段式确认后才提交，提交走 scope=library 且带 mode', () => {
    assert.match(
        MANAGER,
        /gwCall\('preview', 'requestPreviewAllConfirm', previewCacheStart\.dataset\.previewAllMode === 'rebuild' \? 'rebuild' : 'fill'\)/,
        '点击必须按按钮当前语义经网关进入对应确认态'
    );
    assert.match(MANAGER, /if\(target\.closest\?\.\('\[data-preview-all-confirm\]'\)\)\{ await gwCall\('preview', 'startPreviewAllCache'\); return; \}/);
    assert.match(MANAGER, /if\(target\.closest\?\.\('\[data-preview-all-cancel\]'\)\)\{ gwCall\('preview', 'cancelPreviewAllConfirm'\); return; \}/);

    const confirm = functionSource('requestPreviewAllConfirm');
    assert.match(confirm, /confirming:true/, '首次点击只进入确认态，不能直接提交');
    assert.match(confirm, /renderStorageSettingsModal\(\)/);

    const start = functionSource('startPreviewAllCache');
    assert.match(start, /'\/api\/asset-thumbnails\/generate-background'/);
    assert.match(start, /scope:'library'/, '必须显式声明全库模式');
    assert.match(start, /mode/, '重建缓存必须把 mode 带给后端');
    assert.match(start, /method:'POST'/);
});

test('AC3: 忙态显示真实进度，完成通知带新增/跳过/耗时', () => {
    const body = functionSource('renderAssetPreviewSettingsBody');
    assert.match(body, /正在缓存 \$\{processed\}\/\$\{total\}…/, '忙态必须显示「正在缓存 120/950…」样式进度');
    assert.match(body, /disabled/, '运行中按钮必须禁用，避免重复提交');

    const poll = functionSource('pollPreviewAllJob');
    assert.match(poll, /\/api\/asset-thumbnails\/jobs\/\$\{encodeURIComponent\(jobId\)\}/);
    assert.match(poll, /新增/, '完成通知必须回报实际新增数量');
    assert.match(poll, /跳过/, '完成通知必须回报跳过数量（幂等证明）');
    assert.match(poll, /耗时/);
    assert.match(poll, /setStatus\(notice\)/, '完成通知必须同步到状态栏');
});

test('回归护栏：全库预热只能由创作者显式触发', () => {
    // QIN-591：点击派发点 gwCall 化（登记在案），直调只允许定义一次，派发必须经网关。
    const startCalls = MANAGER.match(/startPreviewAllCache\(/g) || [];
    assert.equal(startCalls.length, 1, `startPreviewAllCache 直调只允许定义一处，实际 ${startCalls.length} 处`);
    assert.match(MANAGER, /await gwCall\('preview', 'startPreviewAllCache'\); return; \}/, '点击派发必须经网关且只此一处入口');
    assert.doesNotMatch(MANAGER, /setInterval\([^)]*startPreviewAllCache/);
    const start = functionSource('startPreviewAllCache');
    assert.match(start, /if\(assetPreviewAllState\.submitting\) return;/, '提交中必须早退，避免重复任务');
});
