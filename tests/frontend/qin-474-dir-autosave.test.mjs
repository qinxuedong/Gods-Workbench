import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repoRoot = process.cwd();
const read = (relativePath) =>
  readFileSync(path.join(repoRoot, relativePath), "utf8");

const assetManager = read("web/js/modules/asset-manager.js");
const assetManagerCss = read("web/css/pages/asset-manager.css");

const footerHtml = () => {
  const start = assetManager.indexOf('<footer class="directory-footer">');
  assert.ok(start >= 0, "目录设置页缺少 directory-footer");
  const end = assetManager.indexOf("</footer>", start);
  assert.ok(end > start, "directory-footer 未闭合");
  return assetManager.slice(start, end);
};

test("AC1 目录设置页没有保存目录主按钮，页脚只留状态", () => {
  assert.doesNotMatch(assetManager, /'保存中…' : '保存目录'/);
  assert.doesNotMatch(assetManager, /data-storage-save \$\{directorySettingsSaving/);
  const footer = footerHtml();
  assert.doesNotMatch(footer, /<button/);
  assert.match(footer, /id="assetStatus"/);
  assert.match(footer, /role="status"/);
});

test("AC2 缓存目录区块没有保存缓存目录按钮", () => {
  assert.doesNotMatch(assetManager, /data-directory-cache-save/);
  assert.doesNotMatch(assetManager, /保存缓存目录<\/button>/);
  assert.match(
    assetManager,
    /gw-settings-card">\$\{renderAssetThumbnailCacheDirectoryBody\(\)\}<\/section>/,
  );
});

test("AC3 缓存根目录旁有文件夹选择按钮并复用统一选夹链路", () => {
  assert.match(assetManager, /const THUMBNAIL_DIRECTORY_FIELD_KEY = 'thumbnail_cache'/);
  assert.match(
    assetManager,
    /function directoryFieldInput\(key\)\{[\s\S]*?THUMBNAIL_DIRECTORY_FIELD_KEY[\s\S]*?getElementById\('thumbnailDirectory'\)[\s\S]*?getElementById\('storageDir_' \+ key\)/,
  );
  assert.match(assetManager, /const cacheRootPicker = canBrowseServerDirectories\(\)/);
  assert.match(
    assetManager,
    /data-directory-picker="' \+ THUMBNAIL_DIRECTORY_FIELD_KEY \+ '"/,
  );
  assert.match(
    assetManager,
    /<input id="thumbnailDirectory"[\s\S]*?>\$\{cacheRootPicker\}<\/div><\/label>/,
  );
  // 点击选夹按 key 定位到缓存根输入框，而不是 storageDir_ 前缀字段。
  assert.match(
    assetManager,
    /directoryPicker\.dataset\.directoryPicker;[\s\S]*?const input = directoryFieldInput\(key\);/,
  );
  assert.match(
    assetManager,
    /pickServerDirectoryNative\(key\)\{[\s\S]*?const input = directoryFieldInput\(key\);/,
  );
});

test("AC4 输入 Enter 与选文件夹两条路径都立即持久化", () => {
  // 路径一：Enter。登记目录 / 上传与生成 / 项目工作区 / 回收站走 storageDir_ 前缀，缓存根目录单独识别。
  assert.match(
    assetManager,
    /event\.key === 'Enter'\)\{[\s\S]*?\[id\^="storageDir_"\][\s\S]*?thumbnailDirectory/,
  );
  assert.match(
    assetManager,
    /const key = storageInput\.id === 'thumbnailDirectory'[\s\S]*?THUMBNAIL_DIRECTORY_FIELD_KEY[\s\S]*?storageInput\.id\.replace\('storageDir_', ''\)/,
  );
  assert.match(
    assetManager,
    /saveDirectoryPath\(key, storageInput\.value\)\.catch\(error => setStatus\(error\.message \|\| '保存目录失败'\)\)/,
  );
  // 路径二：选完文件夹立即保存，不再提示手动点保存。
  assert.match(
    assetManager,
    /fillServerDirectoryFromNative\(key, pathValue\)\{[\s\S]*?directoryFieldInput\(key\)[\s\S]*?saveDirectoryPath\(key, pathValue\)/,
  );
  assert.doesNotMatch(assetManager, /点“保存目录”生效/);
  // 页面内服务器目录浏览器选择并挂载同样走保存入口。
  assert.match(
    assetManager,
    /data-server-directory-mount[\s\S]*?saveDirectoryPath\(serverDirectoryBrowserState\.key \|\| 'projects', pathValue, true\)/,
  );
  // 缓存根目录有独立保存入口，保存后落到缩略图设置接口。
  assert.match(assetManager, /async function saveThumbnailCacheDirectory\(pathValue, closeBrowser=false\)/);
  assert.match(
    assetManager,
    /saveThumbnailCacheDirectory\([\s\S]*?persistThumbnailStorageSettings\(\)/,
  );
  assert.match(
    assetManager,
    /saveDirectoryPath\(key, pathValue, closeBrowser=false\)\{[\s\S]*?key === THUMBNAIL_DIRECTORY_FIELD_KEY[\s\S]*?saveThumbnailCacheDirectory\(trimmed, closeBrowser\)/,
  );
});

test("AC4 允许根路径字段回车即登记保存，且有选夹按钮", () => {
  // 允许根不是 storageDir_* / thumbnailDirectory，用独立 key 接入同一套链路。
  assert.match(assetManager, /const ALLOWED_ROOT_FIELD_KEY = 'allowed_root'/);
  assert.match(
    assetManager,
    /function directoryFieldInput\(key\)\{[\s\S]*?ALLOWED_ROOT_FIELD_KEY[\s\S]*?getElementById\('allowedRootPath'\)[\s\S]*?getElementById\('storageDir_' \+ key\)/,
  );
  // 回车：#allowedRootPath 纳入 Enter 处理器并映射到 allowed_root。
  assert.match(
    assetManager,
    /event\.key === 'Enter'\)\{[\s\S]*?event\.target\?\.id === 'allowedRootPath' \? event\.target : null/,
  );
  assert.match(
    assetManager,
    /storageInput\.id === 'allowedRootPath'[\s\S]*?\? ALLOWED_ROOT_FIELD_KEY/,
  );
  // 保存入口：allowed_root 路由到登记逻辑，落到 /api/storage-settings/roots。
  assert.match(
    assetManager,
    /saveDirectoryPath\(key, pathValue, closeBrowser=false\)\{[\s\S]*?if\(key === ALLOWED_ROOT_FIELD_KEY\)\{ await registerAllowedRootPath\(trimmed\); return; \}/,
  );
  assert.match(assetManager, /async function registerAllowedRootPath\(pathValue\)/);
  assert.match(
    assetManager,
    /registerAllowedRootPath\(pathValue\)\{[\s\S]*?saveAllowedRootsRequest\('\/api\/storage-settings\/roots', 'POST', payload\)/,
  );
  assert.match(
    assetManager,
    /const payload = \{path, expected_version: Number\(allowedRootSettingsState\.revision \|\| 1\)\}/,
  );
  // 空值不静默，沿用既有「登记允许根」的空值文案。
  assert.match(assetManager, /error:'请输入服务器绝对路径'/);
  assert.match(assetManager, /setStatus\('请输入服务器绝对路径'\)/);
  // 选夹：允许根输入框旁有选夹按钮，并包进共享行布局。
  assert.match(assetManager, /const allowedRootPicker = canBrowseServerDirectories\(\)/);
  assert.match(
    assetManager,
    /data-directory-picker="' \+ ALLOWED_ROOT_FIELD_KEY \+ '"/,
  );
  assert.match(
    assetManager,
    /<input id="allowedRootPath"[\s\S]*?>\$\{allowedRootPicker\}<\/div><\/label>/,
  );
  // 「登记允许根」按钮是列表登记动作，保留不删，且与回车共用同一条登记路径。
  assert.match(assetManager, /data-storage-root-add[\s\S]*?登记允许根<\/button>/);
  assert.match(
    assetManager,
    /target\.hasAttribute\('data-storage-root-add'\)\)\{[\s\S]*?void registerAllowedRootPath\(path\)/,
  );
});

test("AC1/AC2 自动保存失败不静默且 cache 与 storage 共用保存外壳", () => {
  assert.match(assetManager, /async function withDirectorySaving\(closeBrowser, task\)/);
  assert.match(assetManager, /setStatus\(error\.message \|\| '保存目录失败'\)/);
  assert.match(assetManager, /缓存目录已保存：/);
  assert.match(assetManager, /已挂载并保存缓存目录：/);
  // 空路径要有明确提示，不能静默丢弃；允许留空的回收根目录仍照常保存。
  assert.match(assetManager, /setStatus\('缓存根目录不能为空'\)/);
  assert.match(
    assetManager,
    /if\(!trimmed && !OPTIONAL_DIRECTORY_FIELDS\.has\(key\)\)\{ setStatus\('目录路径不能为空'\); return; \}/,
  );
  assert.match(assetManager, /const OPTIONAL_DIRECTORY_FIELDS = new Set\(\['trash'\]\)/);
});

test("自动保存重渲染后把焦点还给原字段", () => {
  assert.match(
    assetManager,
    /const focusId = host\.contains\(document\.activeElement\) \? String\(document\.activeElement\?\.id \|\| ''\) : '';/,
  );
  assert.match(assetManager, /next\.focus\(\{preventScroll:true\}\)/);
});

test("缓存根目录选夹行在两个页面共用同一套行布局", () => {
  assert.match(
    assetManagerCss,
    /\.storage-dir-row \.directory-input-row \{[\s\S]*?display:flex;/,
  );
  assert.match(
    assetManagerCss,
    /\.storage-dir-row \.directory-input-row input \{[\s\S]*?flex:1 1 auto;/,
  );
});
