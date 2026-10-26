import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repoRoot = process.cwd();
const read = (relativePath) =>
  readFileSync(path.join(repoRoot, relativePath), "utf8");

const assetVault = read("web/css/pages/asset-vault.css");
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