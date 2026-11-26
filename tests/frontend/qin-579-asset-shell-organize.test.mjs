// QIN-579: 资产库左侧栏文件夹整理迁移——子目录树展开、右键菜单、拖拽移动与撤销
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const manager = readAssetManagerAggregate();
const manifest = readFileSync(new URL('./node-files.txt', import.meta.url), 'utf8');

test('AC1: 侧栏结构统一为资源库树（收集箱、挂载资源）与子目录树结构，排除系统预置占位根', () => {
  // 分层语义：收集箱、挂载资源统一树（QIN-586 收口）
  assert.match(controller, /name:\s*'收集箱'/);
  assert.match(controller, /mine:inbox/);
  assert.match(controller, /挂载资源/);
  assert.match(controller, /data-asset-organize-group-new/);
  assert.match(controller, /asset-shell-twisty/);
  assert.match(controller, /data-asset-tree-toggle/);
  assert.match(controller, /asset-shell-nav-subtree/);

  // 排除视频库素材与图片库素材系统占位项
  assert.match(manager, /id !== 'video' && id !== 'image' && name !== '视频库素材' && name !== '图片库素材'/);
  assert.match(controller, /id !== 'video' && id !== 'image' && name !== '视频库素材' && name !== '图片库素材'/);

  // 快照中携带递归子目录树数据
  assert.match(manager, /function serializeAssetRackFolderNode/);
  assert.match(manager, /children: expanded \? tree\.map\(node => serializeAssetRackFolderNode/);
});

test('AC2: 右键菜单提供新建/重命名/在系统中打开/删除，双击支持内联改名', () => {
  // 右键菜单
  assert.match(controller, /['"]assetOrganizeMenu['"]/);
  assert.match(controller, /新建子文件夹/);
  assert.match(controller, /重命名/);
  assert.match(controller, /在系统中打开/);
  assert.match(controller, /删除（进回收站）/);
  assert.match(controller, /data-asset-organize-new/);
  assert.match(controller, /data-asset-organize-rename/);
  assert.match(controller, /data-asset-organize-open/);
  assert.match(controller, /data-asset-organize-delete/);

  // 双击改名
  assert.match(controller, /Number\(e\.detail\) >= 2/);
  assert.match(controller, /beginShellRename/);
  assert.match(controller, /data-asset-organize-rename-input/);
  assert.match(controller, /asset-organize-rename-input/);

  // 在系统中打开调用 Bridge 端点
  assert.match(manager, /function gwAssetTreeOpenInSystem/);
  assert.match(manager, /assetOrganizeOpenFolderInSystem/);
});

test('QIN-597: shell 根菜单显示根动作并转发到共享资源文件夹入口', async () => {
  const start = controller.indexOf('  function openShellOrganizeMenu(target, clientX, clientY) {');
  const end = controller.indexOf('  function openShellCreateDialog(parentKey) {', start);
  assert.ok(start >= 0 && end > start, 'shell organize menu function exists');
  const source = controller.slice(start, end);
  const calls = [];
  const makeElement = () => ({
    children: [], attributes: {}, listeners: {}, style: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, listener) { this.listeners[name] = listener; },
    appendChild(child) { this.children.push(child); },
    remove() {},
  });
  const body = makeElement();
  const document = {createElement:makeElement, getElementById:()=>null, body};
  const window = {innerWidth:1200, innerHeight:800, lucide:{createIcons(){}}};
  const bridge = {canManageResourceFolders:()=>true,executeResourceFolderAction:async (...args)=>{ calls.push(args); }};
  const factory = new Function('document','window','closeShellOrganizeMenu','organizeBridge','openShellCreateDialog','beginShellRename','openShellDeleteDialog','showShellOrganizeToast', `${source}; return openShellOrganizeMenu;`);
  const openMenu = factory(document, window, ()=>{}, ()=>bridge, ()=>{}, ()=>{}, ()=>{}, ()=>{});

  const root = {isRoot:true,key:'mine:root:root_0123456789abcdef01234567',rootId:'root_0123456789abcdef01234567',id:'local_extra_test',aliases:['root_0123456789abcdef01234567'],name:'Film'};
  openMenu(root, 20, 30);
  const rootMenu = body.children.at(-1);
  const remove = rootMenu.children.find(button=>Object.hasOwn(button.attributes,'data-asset-organize-remove-root'));
  const purge = rootMenu.children.find(button=>Object.hasOwn(button.attributes,'data-asset-organize-purge-root'));
  assert.ok(remove && purge, 'root actions are present only in the root menu');
  assert.ok(!remove.disabled && !purge.disabled, 'admin can use root actions');
  await remove.listeners.click({stopPropagation(){}});
  await purge.listeners.click({stopPropagation(){}});
  assert.deepEqual(calls, [[root,'remove'],[root,'purge']]);

  openMenu({...root,isRoot:false}, 20, 30);
  const childMenu = body.children.at(-1);
  assert.equal(childMenu.children.some(button=>Object.hasOwn(button.attributes,'data-asset-organize-remove-root')), false);
  assert.equal(childMenu.children.some(button=>Object.hasOwn(button.attributes,'data-asset-organize-purge-root')), false);
  assert.ok(!childMenu.children.find(button=>Object.hasOwn(button.attributes,'data-asset-organize-delete')).disabled);

  openMenu(root, 20, 30);
  const readOnlyBridge = {canManageResourceFolders:()=>false,executeResourceFolderAction:async (...args)=>{ calls.push(args); }};
  const readOnlyMenu = factory(document, window, ()=>{}, ()=>readOnlyBridge, ()=>{}, ()=>{}, ()=>{}, ()=>{});
  readOnlyMenu(root, 20, 30);
  const readOnlyButtons = body.children.at(-1).children.filter(button=>Object.hasOwn(button.attributes,'data-asset-organize-remove-root') || Object.hasOwn(button.attributes,'data-asset-organize-purge-root'));
  assert.equal(readOnlyButtons.length, 2);
  assert.ok(readOnlyButtons.every(button=>button.disabled), 'read-only users cannot invoke root actions');
});

test('QIN-656 AC3: shell 挂载根菜单仅在就绪且有权限时补跑镜头视觉向量', async () => {
  const start = controller.indexOf('  function openShellOrganizeMenu(target, clientX, clientY) {');
  const end = controller.indexOf('  function openShellCreateDialog(parentKey) {', start);
  assert.ok(start >= 0 && end > start, 'shell organize menu function exists');
  const source = controller.slice(start, end);
  const calls = [];
  const toasts = [];
  const makeElement = () => ({
    children: [], attributes: {}, listeners: {}, style: {}, isConnected: true,
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, listener) { this.listeners[name] = listener; },
    appendChild(child) { this.children.push(child); },
    remove() {},
  });
  const body = makeElement();
  const document = {createElement:makeElement, getElementById:()=>null, body};
  const window = {innerWidth:1200, innerHeight:800, lucide:{createIcons(){}}};
  const bridge = {
    canManageResourceFolders:()=>true,
    canRunShotVectorJob:key=>key === 'mine:root:root_0123456789abcdef01234567',
    getShotVectorStatus:async()=>({enabled:true,ready:true}),
    runShotVectorJob:async (...args)=>{ calls.push(args); return {status:'succeeded',result:{assets:1,shots:2,frames:3}}; },
    executeResourceFolderAction:async()=>{},
  };
  const factory = new Function('document','window','closeShellOrganizeMenu','organizeBridge','openShellCreateDialog','beginShellRename','openShellDeleteDialog','showShellOrganizeToast', `${source}; return openShellOrganizeMenu;`);
  const openMenu = factory(document, window, ()=>{}, ()=>bridge, ()=>{}, ()=>{}, ()=>{}, text=>toasts.push(text));
  const root = {isRoot:true,key:'mine:root:root_0123456789abcdef01234567',rootId:'root_0123456789abcdef01234567',id:'local_extra_test',aliases:['root_0123456789abcdef01234567'],name:'Film'};

  openMenu(root, 20, 30);
  const rootMenu = body.children.at(-1);
  const vectors = rootMenu.children.find(button=>Object.hasOwn(button.attributes,'data-asset-organize-shot-vectors'));
  assert.ok(vectors, 'shell mounted root menu exposes the vector rerun action');
  assert.ok(vectors.disabled, 'vector action stays disabled until status is checked');
  await Promise.resolve();
  assert.equal(vectors.disabled, false, 'ready local model and enabled setting enable the action');
  await vectors.listeners.click({stopPropagation(){}});
  assert.deepEqual(calls, [[root.rootId, root.name]]);
  assert.ok(toasts.some(text=>text.includes('补跑完成')));

  openMenu({...root,isRoot:false}, 20, 30);
  const childMenu = body.children.at(-1);
  assert.equal(childMenu.children.some(button=>Object.hasOwn(button.attributes,'data-asset-organize-shot-vectors')), false);

  const readOnlyBridge = {...bridge,canRunShotVectorJob:()=>false,getShotVectorStatus:async()=>{ throw new Error('status must not be requested'); }};
  const readOnlyMenuFactory = new Function('document','window','closeShellOrganizeMenu','organizeBridge','openShellCreateDialog','beginShellRename','openShellDeleteDialog','showShellOrganizeToast', `${source}; return openShellOrganizeMenu;`);
  const openReadOnlyMenu = readOnlyMenuFactory(document, window, ()=>{}, ()=>readOnlyBridge, ()=>{}, ()=>{}, ()=>{}, ()=>{});
  openReadOnlyMenu(root, 20, 30);
  const readOnlyVector = body.children.at(-1).children.find(button=>Object.hasOwn(button.attributes,'data-asset-organize-shot-vectors'));
  assert.ok(readOnlyVector.disabled, 'read-only roots cannot run the vector action');

  const notReadyBridge = {...bridge,getShotVectorStatus:async()=>({enabled:true,ready:false,message:'本地视觉模型或索引尚未就绪'})};
  const openNotReadyMenu = factory(document, window, ()=>{}, ()=>notReadyBridge, ()=>{}, ()=>{}, ()=>{}, ()=>{});
  openNotReadyMenu(root, 20, 30);
  const notReadyVector = body.children.at(-1).children.find(button=>Object.hasOwn(button.attributes,'data-asset-organize-shot-vectors'));
  await Promise.resolve();
  assert.ok(notReadyVector.disabled, 'missing local model or index keeps the action disabled');
  assert.match(notReadyVector.title, /尚未就绪/);
  assert.match(manager, /canRunShotVectorJob,/);
  assert.match(manager, /getShotVectorStatus,/);
  assert.match(manager, /runShotVectorJob,/);
});

test('AC3: 拖拽移动——文件夹拖拽落点判定与跨窗口素材卡片拖拽移动', () => {
  // 文件夹拖拽属性与落点样式
  assert.match(controller, /data-asset-tree-drag/);
  assert.match(controller, /is-organize-drop-into/);
  assert.match(controller, /is-organize-drop-before/);
  assert.match(controller, /is-organize-drop-after/);
  assert.match(controller, /bindShellDragEvents/);
  assert.match(controller, /moveFolder/);
  assert.match(controller, /moveAssets/);

  // 素材卡片拖拽状态跨窗口共享
  assert.match(manager, /window\.parent\.GWAssetDragState = assetOrganizeDrag/);
  assert.match(manager, /window\.parent\.GWAssetDragState = null/);
});

test('AC4: 整理弹窗事件委托、重名冲突与 Ctrl+Z 撤销 Toast', () => {
  // 弹窗与事件委托：支持取消、关闭与创建可靠触发
  assert.match(controller, /['"]assetOrganizeDialogOverlay['"]/);
  assert.match(controller, /id="assetOrganizeDialog"/);
  assert.match(controller, /data-asset-organize-dialog-confirm/);
  assert.match(controller, /data-asset-organize-dialog-cancel/);
  assert.match(controller, /closest\('\[data-asset-organize-dialog-cancel\]'\)/);
  assert.match(controller, /closest\('\[data-asset-organize-dialog-confirm\]'\)/);

  // 桥接方法预载物理路径
  assert.match(manager, /await loadAssetAllowedRootPaths\?\.\(\);[\s\S]{0,100}assetOrganizeTargetFromKey/);

  // 撤销 Toast
  assert.match(controller, /['"]assetOrganizeToast['"]/);
  assert.match(controller, /asset-organize-toast/);
  assert.match(controller, /data-asset-organize-undo-now/);

  // Ctrl+Z 快捷键捕获
  assert.match(controller, /\(e\.ctrlKey \|\| e\.metaKey\) && e\.key\.toLowerCase\(\) === 'z'/);
  assert.match(controller, /organizeBridge\(\)\?\.undoLast/);
});

test('AC5: 收集箱与自建看板不响应整理菜单', () => {
  assert.match(manager, /function assetOrganizeTargetFromKey/);
  assert.match(controller, /organizeBridge\(\)\?\.canOrganize/);
  // 右键菜单仅在 canOrganize 时弹出
  assert.match(controller, /if\s*\(organizeBridge\(\)\?\.canOrganize\(row\.key\)\)\s*\{[\s\S]{0,100}openShellOrganizeMenu/);
});

test('AC6: 桥接对象 GWAssetOrganizeBridge 接口完整导出与清单登记', () => {
  assert.match(manager, /window\.GWAssetOrganizeBridge = Object\.freeze\(\{/);
  for (const method of ['toggleExpand', 'canOrganize', 'getTarget', 'executeResourceFolderAction', 'openInSystem', 'createFolder', 'commitRename', 'deleteFolder', 'moveFolder', 'moveAssets', 'undoLast']) {
    assert.match(manager, new RegExp(`${method}:`));
  }
  assert.match(manager, /executeResourceFolderAction: \(rootTarget, action\) => gwCall\('organize', 'executeResourceFolderAction', rootTarget, action, \{source:'sidebar'\}\)/);
  assert.ok(manifest.includes('tests/frontend/qin-579-asset-shell-organize.test.mjs'), '测试需登记在 node-files.txt');
});
