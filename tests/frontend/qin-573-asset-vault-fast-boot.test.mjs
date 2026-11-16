// QIN-573: 资产库首屏秒开：父页直连子帧与 SWR 快照优先渲染
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const assetsHtml = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const assetsControllerJs = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const assetManagerHtml = readFileSync(new URL('../../web/embeds/asset-manager.html', import.meta.url), 'utf8');
const assetManagerJs = readAssetManagerAggregate();

test('AC1: assets.html 的 iframe#assetVaultFrame 原生直出 src，并发加载避免串行阻塞', () => {
  // 必须原生包含 src 与 data-src，且 src 指向内嵌金库页
  assert.match(
    assetsHtml,
    /<iframe\s+id="assetVaultFrame"[^>]*src="\/static\/asset-manager\.html\?embedded=1&amp;vault=1"[^>]*>/,
    'iframe 必须原生携带初始 src 属性以实现并发加载'
  );
  assert.match(
    assetsHtml,
    /<iframe\s+id="assetVaultFrame"[^>]*data-src="\/static\/asset-manager\.html\?embedded=1&amp;vault=1"[^>]*>/,
    'iframe 必须保留 data-src 供控制器参数同步'
  );
});

test('AC1: assets-controller.js syncFrameSource 幂等赋值，不触发无谓的 iframe 二次重载', () => {
  assert.match(
    assetsControllerJs,
    /function syncFrameSource\(\)\s*\{[\s\S]*?const currentHref = frame\.getAttribute\('src'\) \|\| frame\.src \|\| '';[\s\S]*?if\s*\(currentUrl\.href !== source\.href\)\s*\{\s*frame\.src = source\.href;\s*\}/,
    'syncFrameSource 必须检查当前 src 是否已匹配目标 URL，避免二次重置'
  );
});

test('AC2: embeds/asset-manager.html 包含快照探针，命中缓存时首屏骨架转圈立即 hidden', () => {
  assert.match(
    assetManagerHtml,
    /sessionStorage\.getItem\('gw_asset_registry_snapshot_v1'\)[\s\S]*?_sk\.hidden = true/,
    'HTML 中必须内置快照直出探针，命中缓存时第 1 毫秒隐藏全屏骨架屏'
  );
});

test('AC2 & AC3: asset-manager.js 实现 SWR 快照存储、即时恢复与桥接导出', () => {
  // 1. 常量定义
  assert.match(assetManagerJs, /const REGISTRY_SNAPSHOT_KEY = 'gw_asset_registry_snapshot_v1';/);

  // 2. 导出到 GWAssetRegistrySnapshot
  assert.match(
    assetManagerJs,
    /window\.GWAssetRegistrySnapshot = Object\.freeze\(\{save:saveRegistrySnapshot, apply:tryApplyRegistrySnapshot\}\);/
  );

  // 3. 快照保存逻辑
  assert.match(
    assetManagerJs,
    /function saveRegistrySnapshot\(\)\s*\{[\s\S]*?sessionStorage\.setItem\(REGISTRY_SNAPSHOT_KEY, JSON\.stringify\(snapshot\)\);/,
    '必须实现 saveRegistrySnapshot 写入 sessionStorage'
  );

  // 4. 快照读取与即时挂载逻辑
  assert.match(
    assetManagerJs,
    /function tryApplyRegistrySnapshot\(\)\s*\{[\s\S]*?sessionStorage\.getItem\(REGISTRY_SNAPSHOT_KEY\)[\s\S]*?skeleton\.hidden = true;[\s\S]*?render\(\);/,
    'tryApplyRegistrySnapshot 必须恢复数据、隐藏骨架并执行 render()'
  );

  // 5. 引导完成与会话保存时自动沉淀快照
  assert.match(
    assetManagerJs,
    /applyRegistryAssetResponse\(assetData\);[\s\S]*?saveRegistrySnapshot\(\);/,
    '首屏数据到达后必须保存快照'
  );
  assert.match(
    assetManagerJs,
    /function saveRegistryBrowseSession\(\)\s*\{[\s\S]*?saveRegistrySnapshot\(\);/,
    '保存浏览会话时必须同步沉淀快照'
  );
});

test('AC4: 首次冷启动（无快照）安全降级，渲染完成后骨架屏确保隐藏', () => {
  assert.match(
    assetManagerJs,
    /const bootSkeleton = document\.getElementById\('assetManagerBootSkeleton'\);\s*if\(bootSkeleton\) bootSkeleton\.hidden = true;/,
    'render() 完成时必须确保骨架屏 hidden'
  );
});

test('AC5: 首屏缩略图即时加载，避免二次无谓重绘与清空队列', () => {
  // 1. 并发度调优至 8
  assert.match(
    assetManagerJs,
    /const REGISTRY_PREVIEW_CONCURRENCY = 8;/,
    '并发度提升至 8 以加速滚动批次下载'
  );

  // 2.【QIN-609】immediate 直加载路径已删除：全部缩略图统一走 defer 占位 + 8 并发队列
  //   （票面 AC1 并发上限要求首屏卡片也受队列管控，不再绕过并发上限直出真实 src）。
  assert.match(
    assetManagerJs,
    /function deferRegistryPreviewThumb\(markup\)\s*\{/,
    'deferRegistryPreviewThumb 单参签名：全部缩略图统一走延迟队列'
  );
  assert.doesNotMatch(assetManagerJs, /if\(immediate\)/, 'immediate 直加载分支已删除');

  // 3. renderRegistryCard 首屏卡片同样 defer（无 index 直出参数）
  assert.match(
    assetManagerJs,
    /deferRegistryPreviewThumb\(assetThumb\(item\)\)/,
    'renderRegistryCard 前 24 张卡片必须开启即时直出'
  );

  // 4. loadRegistryBootstrap 数据未变时跳过二次全量 render()
  assert.match(
    assetManagerJs,
    /const prevAssetFingerprint = registryAssets\.map[\s\S]*?const newAssetFingerprint = registryAssets\.map[\s\S]*?const assetsChanged = prevAssetFingerprint !== newAssetFingerprint \|\| !hasMountedCards;[\s\S]*?if\(assetsChanged[\s\S]*?render\(\);[\s\S]*?\} else \{[\s\S]*?syncRegistryVaultFooter\(\);[\s\S]*?\}/,
    '数据无变化且卡片已挂载时禁止推倒 DOM 二次 render，防止打断首屏缩略图网络连接'
  );
});
