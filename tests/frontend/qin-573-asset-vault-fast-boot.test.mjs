// QIN-573: 资产库首屏秒开：父页直连子帧与 SWR 快照优先渲染
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const assetsHtml = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const assetsControllerJs = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const assetManagerHtml = readFileSync(new URL('../../web/embeds/asset-manager.html', import.meta.url), 'utf8');
const assetManagerJs = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');

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
