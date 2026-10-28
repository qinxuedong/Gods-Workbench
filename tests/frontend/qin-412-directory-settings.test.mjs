import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repoRoot = process.cwd();
const read = (relativePath) =>
  readFileSync(path.join(repoRoot, relativePath), "utf8");

const assetVault = read("web/css/pages/asset-vault.css");
const assetManager = read("web/js/modules/asset-manager.js");
const directorySettings = read("web/css/pages/directory-settings.css");
const templateLayout = () => {
  const start = assetVault.indexOf("/* 项目目录模板管理浮窗");
  const end = assetVault.indexOf(
    "/* Floating asset surfaces inherit the same hardware palette. */",
    start,
  );

  assert.ok(start >= 0 && end > start, "asset-vault.css 缺少迁移后的模板布局段");
  return assetVault.slice(start, end);
};

test("目录模板弹窗具备完整布局、窄屏与减少动效规则", () => {
  const layout = templateLayout();

  assert.match(layout, /\.project-directory-template-sheet\s*\{[\s\S]*?width:\s*min\(1120px/);
  assert.match(layout, /\.project-directory-template-sheet\s*\{[\s\S]*?height:\s*min\(860px/);
  assert.match(layout, /\.project-directory-template-sheet\s*\{[\s\S]*?display:\s*flex/);
  assert.match(layout, /\.project-directory-template-sheet-body\s*\{[\s\S]*?display:\s*grid[\s\S]*?grid-template-columns:/);
  assert.match(layout, /@media\s*\(max-width:\s*760px\)[\s\S]*?\.project-directory-template-sheet-body\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(layout, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.project-directory-template-manager-loading > svg\s*\{[^}]*animation:\s*none/);
  assert.match(layout, /@keyframes vault-directory-template-spin/);
  assert.doesNotMatch(layout, /var\(--sf-/);
  assert.match(layout, /var\(--vault-/);
  assert.match(layout, /var\(--gw-/);
});

test("模板弹窗继续由资产主题提供亮色覆盖", () => {
  assert.match(
    assetVault,
    /html\[data-theme="light"\] body\.gw-signal\.gw-page--asset-manager :is\(\.storage-settings-modal,\.project-directory-template-sheet,/,
  );
  assert.match(
    assetVault,
    /html\[data-theme="light"\] body\.gw-signal\.gw-page--asset-manager :is\(\.storage-settings-head,\.project-directory-template-sheet-head,/,
  );
});

test("资产管理内嵌页加载 asset-vault 且不加载 signal-flow", () => {
  const entry = read("web/embeds/asset-manager.html");

  assert.match(entry, /asset-vault\.css/);
  assert.doesNotMatch(entry, /signal-flow\.css/);
});

test("目录设置移除空托管框并使用服务器目录即选即存", () => {
  assert.doesNotMatch(assetManager, /managed_name|managed_path/);
  assert.doesNotMatch(
    assetManager,
    /openDirectoryMountConfirmDialog|directoryMountDialogOverlay|data-directory-mount-confirm/,
  );
  assert.match(assetManager, /api\/storage-settings\/browse/);
  assert.match(assetManager, /data-server-directory-browser/);
  assert.match(assetManager, /data-server-directory-mount/);
  assert.match(assetManager, /canBrowseServerDirectories[\s\S]*?=== 'admin'/);
  assert.doesNotMatch(directorySettings, /\.directory-mount-/);
});

test("QIN-432 服务端字段优先原生选择器并保留页面内降级", () => {
  assert.match(assetManager, /api\/settings\/directories\/pick-native/);
  assert.match(assetManager, /pickServerDirectoryNative/);
  // 选中填入字段并提示手动保存；取消不改字段；不可用降级页面内浏览器。
  assert.match(assetManager, /fillServerDirectoryFromNative/);
  assert.match(assetManager, /已选择目录：[\s\S]*?保存目录/);
  assert.match(assetManager, /已切换页面内浏览/);
  // 非回环 403 与请求失败都进入降级路径。
  assert.match(
    assetManager,
    /pickServerDirectoryNative[\s\S]*?catch\(error\)[\s\S]*?openServerDirectoryBrowser\(key\)/,
  );
});

test("QIN-432 目录设置卡片统一双列并在窄屏折单列", () => {
  assert.match(directorySettings, /\.directory-cards-grid\s*\{[\s\S]*?grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(
    directorySettings,
    /@media\s*\(max-width:\s*700px\)[\s\S]*?\.directory-cards-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/,
  );
  assert.match(assetManager, /<fieldset class="directory-cards-grid"/);
  // 页面级服务器目录浏览器只在目录设置页渲染在页脚之前，不再嵌进项目工作区卡片。
  assert.match(assetManager, /<\/fieldset>\$\{directorySettingsPage \? renderServerDirectoryBrowser\(\) : ''\}/);
  assert.match(assetManager, /directorySettingsPage \? '' : renderServerDirectoryBrowser\(\)/);
});

test("QIN-432 客户端模式检测与客户端来源目录只存本机", () => {
  assert.match(assetManager, /function isTauriClientMode\(\)/);
  assert.match(assetManager, /window\.__TAURI__\?\.core\?\.invoke \|\| window\.top\?\.__TAURI__/);
  assert.match(assetManager, /data-client-directory-picker/);
  assert.match(assetManager, /id="clientUploadSourceDir"/);
  assert.match(assetManager, /pick_client_folder/);
  assert.match(assetManager, /get_client_setting/);
  assert.match(assetManager, /set_client_setting/);
  assert.match(assetManager, /CLIENT_SETTING_KEY_UPLOAD_SOURCE = 'upload_source_dir'/);
  // 客户端上传走客户端对话框 + 会话白名单读取命令，浏览器模式不受影响。
  assert.match(assetManager, /pick_client_upload_files/);
  assert.match(assetManager, /read_client_file/);
  assert.match(assetManager, /uploadViaClientPicker/);
  assert.match(
    assetManager,
    /data-asset-upload\][\s\S]*?isTauriClientMode\(\)[\s\S]*?uploadViaClientPicker/,
  );
  // 客户端字段仅在客户端模式渲染。
  assert.match(assetManager, /\$\{isTauriClientMode\(\) \? clientSourceInput\(\) : ''\}/);
});
