import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// QIN-473（GitHub #444）：目录设置 / API与密钥配置右侧容器背景须与「通用与外观偏好」一致，
// 只让设置板块卡片保持黑底，不允许整片近黑铺底。
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = relativePath => readFileSync(path.join(root, relativePath), 'utf8');

const sharedCss = read('web/css/pages/settings-workbench.css');
const directoryCss = read('web/css/pages/directory-settings.css');
const apiCss = read('web/css/pages/api-settings.css');

// 1. 共享容器底色 token：与 .champagne-card 同梯度，供两个内嵌子页复用。
assert.match(
  sharedCss,
  /--gw-settings-container-background:\s*linear-gradient\(165deg,\s*var\(--card-start\)\s*0%,\s*var\(--card-end\)\s*100%\)/,
  '共享设置容器底色应与 champagne-card 同梯度'
);

// 2. 目录设置：嵌入设置工作台时页面基底改用共享容器底色。
assert.match(
  directoryCss,
  /html\.directory-settings-page\.workspace-embedded\s*\{[^}]*background:\s*var\(--gw-settings-container-background\)/s,
  '嵌入态目录设置 html 应使用共享容器底色'
);
// signal-flow.css 对资产路由 body 有 !important 锁底色，嵌入态需用更高权重覆盖。
assert.match(
  directoryCss,
  /html\.directory-settings-page\.workspace-embedded\s+body\.gw-signal\.gw-page--asset-manager\s*\{[^}]*background:\s*var\(--gw-settings-container-background\)\s*!important/s,
  '嵌入态目录设置 body 应使用共享容器底色并压过 signal-flow 锁底'
);

// 3. API与密钥配置：嵌入态与目录页同口径。
assert.match(
  apiCss,
  /html\.workspace-embedded\s*\{[^}]*background:\s*var\(--gw-settings-container-background\)/s,
  '嵌入态 API 设置 html 应使用共享容器底色'
);
assert.match(
  apiCss,
  /\.workspace-embedded\s+body\.api-settings-page\s*\{[^}]*background:\s*var\(--gw-settings-container-background\)/s,
  '嵌入态 API 设置 body 应使用共享容器底色'
);

// 4. 卡片/板块仍保持黑底：容器改色不得把设置板块一起提亮。
assert.match(
  directoryCss,
  /\.directory-page\s+\.asset-pref-section\s*\{[^}]*background:\s*var\(--gw-settings-card-background\)/s,
  '目录设置板块卡片应保持黑底'
);
assert.match(
  apiCss,
  /\.workspace-embedded\s+\.api-settings-page\s+\.block\s*\{[^}]*background:var\(--gw-settings-card-background\)/s,
  'API 设置板块卡片应保持黑底'
);

// 5. 不得为对齐而引入硬编码纯黑铺底（#000 / rgb(0,0,0)）。
for (const [name, css] of [['directory-settings.css', directoryCss], ['api-settings.css', apiCss]]) {
  assert.doesNotMatch(css, /background:\s*(?:#000|#000000|rgb\(\s*0\s*,\s*0\s*,\s*0\s*\))\s*[;}]/, `${name} 不得新增纯黑铺底`);
}

console.log('QIN-473 settings background alignment contracts passed');
