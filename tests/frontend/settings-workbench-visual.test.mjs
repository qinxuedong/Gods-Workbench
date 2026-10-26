import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = relativePath => readFileSync(path.join(root, relativePath), 'utf8');
const settings = read('web/pages/settings.html');
const directoryModule = read('web/js/modules/asset-manager.js');
const directoryCss = read('web/css/pages/directory-settings.css');
const apiHtml = read('web/embeds/api-settings.html');
const apiCss = read('web/css/pages/api-settings.css');
const sharedCss = read('web/css/pages/settings-workbench.css');

function panelHtml(name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const panel = settings.match(new RegExp(`<section\\b(?=[^>]*\\bdata-panel="${escaped}")[^>]*>([\\s\\S]*?)<\\/section>`, 'i'));
  assert.ok(panel, `设置页应保留 ${name} 面板`);
  return panel[1];
}

for (const name of ['general', 'prompt-sources', 'api-settings', 'permissions']) {
  const header = panelHtml(name).match(/<(?:div|header)\b[^>]*\bgw-settings-panel-head\b[^>]*>/i);
  assert.ok(header, `${name} 应复用公共标题区`);
  assert.doesNotMatch(header[0], /\bborder-b\b/, `${name} 标题下不得有分割线`);
}

const settingsIcons = [...settings.matchAll(/<i\b[^>]*\bclass="([^"]*)"[^>]*>/gi)];
assert.ok(settingsIcons.length > 0);
assert.doesNotMatch(settingsIcons.map(match => match[1]).join(' '), /\btext-(?:cyan|emerald)-\d+\b/, '设置页装饰图标应使用金色');
assert.doesNotMatch(panelHtml('permissions'), /(?:bg|border|text|group-hover:text)-cyan-/, '权限快捷卡不得保留蓝色装饰');

assert.match(settings, /src="\/static\/asset-manager\.html\?settings=directories&embedded=1"/);
assert.match(settings, /src="\/static\/api-settings\.html\?embedded=1"/);
assert.match(directoryModule, /<header class="directory-page-header gw-settings-panel-head">/);
assert.match(directoryModule, /class="asset-pref-section bay-inset p-4 rounded-xl gw-settings-card"/);
assert.match(directoryCss, /max-width:\s*none/);
assert.match(directoryCss, /border:\s*0;/);
assert.match(apiHtml, /class="content-head gw-settings-panel-head"/);
assert.match(apiCss, /\.workspace-embedded\s+\.api-settings-page\s+\.content\s*\{[^}]*background:transparent/s);
assert.match(apiCss, /\.workspace-embedded\s+\.api-settings-page\s+\.block\s*\{[^}]*background:var\(--gw-settings-card-background\)/s);
assert.match(sharedCss, /\.gw-settings-panel-head\s*\{/);
assert.match(sharedCss, /\.gw-settings-card\s*\{/);

console.log('QIN-411 settings workbench visual contracts passed');
