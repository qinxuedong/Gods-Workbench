/** QIN-198：顶栏 SPA 局部换页的工作区边界与控制器重绑契约。 */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const read = path => readFileSync(path, 'utf8');
const shell = read('web/js/core/workbench-shell.js');

const workspacePages = [
  ['index.html', 'homeWorkspace'],
  ['projects.html', 'projectsWorkspace'],
  ['production.html', 'productionWorkspace'],
  ['workshop.html', 'workshopWorkspace'],
  ['storyboard.html', 'storyboardWorkspace'],
  ['collab.html', 'collabWorkspace']
];

test('C8-FIX-198 页面工作区使用显式壳层边界，避免误选内层 main', () => {
  for (const [page, id] of workspacePages) {
    const html = read(`web/pages/${page}`);
    const opening = html.match(new RegExp(`<[^>]+id="${id}"[^>]*>`))?.[0];
    assert.ok(opening, `${page} 必须保留 ${id}`);
    assert.match(opening, /\bapp-shell-workspace\b/, `${page} 的 ${id} 必须标记为完整工作区`);
  }
});

test('C8-FIX-198 首页控制条属于 homeWorkspace，离开首页时随工作区移除', () => {
  const html = read('web/pages/index.html');
  const workspaceStart = html.indexOf('id="homeWorkspace"');
  const controlsStart = html.indexOf('id="homeContextBusControls"');
  const asideStart = html.indexOf('id="homeContextBusAside"');
  assert.ok(workspaceStart >= 0, '首页必须存在 homeWorkspace');
  assert.ok(controlsStart > workspaceStart, '首页控制条必须位于 homeWorkspace 内');
  assert.ok(asideStart > controlsStart, '首页控制条应在上下文总线侧栏之前，保持自身可见性控制');
});

test('C8-FIX-198 targetWorkspace 优先完整壳层与顶栏后的根节点，再回退内层 main', () => {
  const start = shell.indexOf('function targetWorkspace');
  const end = shell.indexOf('function firstSidebar', start);
  assert.ok(start >= 0 && end > start, '必须保留 targetWorkspace 实现');
  const body = shell.slice(start, end);
  const explicitShell = body.indexOf("doc.querySelector('.app-shell-workspace')");
  const adjacentRoot = body.indexOf('.topbar-master-deck');
  const mainFallback = body.indexOf("doc.querySelector('#homeWorkspace, #workspace, #agentsWorkspace, main");
  assert.ok(explicitShell >= 0, 'targetWorkspace 必须识别显式工作区壳层');
  assert.ok(adjacentRoot >= 0, 'targetWorkspace 必须识别顶栏后的完整根节点');
  assert.ok(mainFallback >= 0, 'targetWorkspace 必须保留 main 兼容回退');
  assert.ok(explicitShell < mainFallback, '显式工作区必须优先于内层 main');
  assert.ok(adjacentRoot < mainFallback, '顶栏后的完整根节点必须优先于内层 main');
});

test('C8-FIX-198 路由回放不再用全局脚本缓存阻断外部控制器重绑', () => {
  const start = shell.indexOf('async function runRouteScripts');
  const end = shell.indexOf('function disposeRouteController', start);
  assert.ok(start >= 0 && end > start, '必须保留 runRouteScripts 实现');
  const body = shell.slice(start, end);
  assert.doesNotMatch(
    body,
    /if\s*\(executedRouteScriptKeys\.has\(key\)\)\s*continue/,
    '外部控制器脚本不能因曾经执行过而永久跳过'
  );
  assert.match(body, /refreshRouteController|rebind/, '路由回放必须触发控制器重绑');
});

test('C8-FIX-198 切页始终替换完整工作区节点，保留目标页根 ID 与侧边栏', () => {
  const start = shell.indexOf('async function navigate');
  const end = shell.indexOf('function bindNavigation', start);
  assert.ok(start >= 0 && end > start, '必须保留 navigate 实现');
  const body = shell.slice(start, end);
  assert.doesNotMatch(body, /currentWorkspace\.matches\(['"]main['"]\)/, '不能按 div/main 类型走两套残缺替换路径');
  assert.match(body, /currentWorkspace\.replaceWith\(document\.importNode\(nextWorkspace, true\)\)/);
  assert.match(body, /syncRouteDeck\(doc\)/, '完整换页仍须同步目标页顶栏菜单');
});
