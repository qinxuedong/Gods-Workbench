// QIN-567（GitHub #642）：画布素材索引接线——「全部画布」视图列表呈现契约。
// 后端 GET /api/canvas-assets 的 items 已接入素材注册表与画布产物；本测试为
// 静态契约（不执行 JS），断言 asset-manager.js 的渲染链路真实消费后端下发的
// items 字段（id / canvas_id / canvas_kind / canvas_title / name / url / kind），
// 后端 /api/asset-registry/assets/{asset_id}/media 地址经 assetThumb 直通预览，
// 空库仍保留既有空态文案，类型徽标与后端 kind 映射同口径。
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const js = readAssetManagerAggregate();

// 提取完整函数体，断言函数存在且内部消费点成立（与 qin-544 同一抽取口径）。
function functionSource(name){
    const marker = `function ${name}(`;
    const start = js.indexOf(marker);
    assert.ok(start >= 0, `函数应存在：${name}`);
    let depth = 0;
    for(let index = js.indexOf('{', start); index < js.length; index += 1){
        if(js[index] === '{') depth += 1;
        else if(js[index] === '}'){
            depth -= 1;
            if(depth === 0) return js.slice(start, index + 1);
        }
    }
    assert.fail(`函数未闭合：${name}`);
}

// AC2-1：列表数据源为后端索引 items；分类与画布两级过滤消费 canvas_kind / canvas_id。
const listSource = functionSource('currentCanvasAssetItems');
assert.ok(listSource.includes('canvasAssetsData.items'), '列表必须消费后端 items 数据源');
assert.ok(
    listSource.includes("(item.canvas_kind || 'classic') !== activeCanvasAssetCategory"),
    '分类过滤必须按后端 canvas_kind 同口径兜底 classic'
);
assert.ok(
    listSource.includes('item.canvas_id !== activeCanvasAssetCanvasId'),
    '画布分支过滤必须消费 item.canvas_id'
);
// 去重按 画布+URL 组合键，防止同一节点重复占位。
const uniqueSource = functionSource('uniqueCanvasAssets');
assert.ok(uniqueSource.includes('item.canvas_id'), '去重键必须包含 canvas_id');
assert.ok(uniqueSource.includes('item.url'), '去重键必须包含 url');
// 【QIN-567 修复】url 如实为空（未登记媒体载体的注册表素材）时按资产身份保留，
// 不得因缺 url 丢条目——列表呈现口径与后端零伪造语义一致。
assert.ok(
    uniqueSource.includes('item.asset_id'),
    '去重必须接受 asset_id 作为身份兜底（url 留空的登记素材不丢条目）'
);
assert.ok(
    uniqueSource.includes("item?.url && !item?.asset_id && !item?.id"),
    '必须显式保留「url 留空但有稳定身份」的条目，仅丢弃无可标识事实的条目'
);

// AC2-2：资产卡片消费真实字段——缩略图直通 assetThumb，名称与来源画布同屏。
const cardSource = functionSource('renderCanvasAssetCard');
assert.ok(cardSource.includes('assetThumb(item)'), '卡片缩略图必须复用 assetThumb(item) 通道');
assert.ok(cardSource.includes('item.name'), '卡片必须展示 item.name');
assert.ok(cardSource.includes('item.canvas_title'), '卡片必须展示 item.canvas_title 来源画布');

// AC2-3：后端下发的 item.url（含 /api/asset-registry/assets/{asset_id}/media）
// 不经前端二次拼接，直接进入 assetThumb 的 assetPreviewUrl 预览链路。
const thumbSource = functionSource('assetThumb');
assert.ok(
    thumbSource.includes('assetPreviewUrl(item.url'),
    'assetThumb 必须把后端 item.url 直通 assetPreviewUrl 预览通道'
);
assert.ok(
    js.includes('/api/asset-registry/assets/'),
    '媒体回读地址口径必须与后端 /api/asset-registry/assets/{asset_id}/media 一致'
);

// renderCanvasAssetsManager 模板内存在既有孤立 `}`（`${renderAssetTreePanel()}}`），
// 花括号配对抽取会提前截断；改按顶层函数边界切片，保证取到完整函数体。
function topLevelFunctionSpan(name){
    const start = js.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `函数应存在：${name}`);
    const next = js.indexOf('\nfunction ', start + 1);
    return js.slice(start, next === -1 ? js.length : next);
}

// AC2-4：空态文案保留；【QIN-569 AC4】空态语义收紧——只有真正没有画布对象时才显示空态，
// 有画布（含 0 素材画布）必须展示画布对象卡片区（renderCanvasObjectBoard）而非空态。
const managerSource = topLevelFunctionSpan('renderCanvasAssetsManager');
assert.ok(
    managerSource.includes('当前分类没有可下载的画布资产。'),
    '空态文案必须保留'
);
assert.ok(
    managerSource.includes("categoryCanvases.length ? '' : '<div class=\"empty-state\">"),
    '空态必须仅在当前分类没有画布对象时渲染（QIN-569 AC4：0 素材画布不触发空态）'
);
assert.ok(
    managerSource.includes('${renderCanvasObjectBoard(categoryCanvases)}'),
    '画布资产视图必须渲染画布对象卡片区（QIN-569 AC4）'
);
assert.ok(
    managerSource.includes('canvasAssetsForCategory(activeCanvasAssetCategory)'),
    '画布对象卡片数据源必须来自当前分类的画布列表（QIN-569 AC4）'
);

// 【QIN-569 AC4】画布对象卡片区：消费 canvases + items 聚合计数，点击复用画布行分发属性。
const objectBoardSource = functionSource('renderCanvasObjectBoard');
assert.ok(
    objectBoardSource.includes('canvasAssetCountForCanvas(canvasId)'),
    '画布对象卡片必须按 items 聚合展示每张画布的素材数'
);
assert.ok(
    objectBoardSource.includes('data-canvas-asset-canvas='),
    '画布对象卡片必须复用画布行分发属性（data-canvas-asset-canvas）'
);
assert.ok(
    objectBoardSource.includes('data-canvas-asset-canvas-cat='),
    '画布对象卡片必须携带所属分类（data-canvas-asset-canvas-cat）'
);
assert.ok(
    objectBoardSource.includes("activeCanvasAssetCanvasId === canvasId"),
    '画布对象卡片选中态必须跟随当前聚焦画布'
);
assert.ok(
    objectBoardSource.includes('canvasKindLabel(canvas.kind)'),
    '画布对象卡片必须展示画布类型（智能/普通/参考）'
);

// AC2-5：视图副标题消费 items.length（「N 个资产」口径与统计卡/树分支一致）。
const subtitleSource = functionSource('canvasAssetViewSubtitle');
assert.ok(
    subtitleSource.includes('${items.length} 个资产'),
    '副标题必须展示 items.length 个资产计数'
);

// AC2-6：类型徽标映射与后端 kind 映射同口径（video/audio/text，其余归图片）。
const kindLabelSource = functionSource('canvasAssetKindLabel');
assert.ok(kindLabelSource.includes("'video'"), '视频徽标映射必须存在');
assert.ok(kindLabelSource.includes("'audio'"), '音频徽标映射必须存在');
assert.ok(kindLabelSource.includes("'text'"), '文本徽标映射必须存在');
assert.ok(kindLabelSource.includes("'图片'"), '默认徽标必须归入图片');

// AC2-7：树分支逐画布计数消费 item.canvas_id，与「画布」统计卡口径一致。
const countSource = functionSource('canvasAssetCountForCanvas');
assert.ok(
    countSource.includes('item.canvas_id === canvasId'),
    '逐画布计数必须按 item.canvas_id 聚合'
);
const treeBranchSource = functionSource('renderCanvasAssetTreeBranch');
assert.ok(
    treeBranchSource.includes('canvasAssetCountForCanvas(canvas.id)'),
    '树分支子行必须渲染逐画布资产计数'
);
