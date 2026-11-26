// QIN-579: 资产库左侧栏文件夹整理迁移——子目录树展开、右键菜单、拖拽移动与撤销
import assert from 'node:assert/strict';
import {createContext, runInContext} from 'node:vm';
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
  const dashed = value => String(value).replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);
  const matches = (node, selector) => {
    if (selector.startsWith('#')) return node.id === selector.slice(1);
    if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
    if (selector.startsWith('[') && selector.endsWith(']')) {
      const [name, expected] = selector.slice(1, -1).split('=');
      const attribute = name.trim();
      if (!node.hasAttribute(attribute)) return false;
      return expected === undefined || node.getAttribute(attribute) === expected.slice(1, -1);
    }
    return false;
  };
  class TestElement {
    constructor(tagName, ownerDocument, nodeType = 1) {
      this.tagName = String(tagName).toUpperCase();
      this.ownerDocument = ownerDocument;
      this.nodeType = nodeType;
      this.parentNode = null;
      this.childNodes = [];
      this.attributes = new Map();
      this.listeners = new Map();
      this.style = {};
      this._textContent = '';
      this._innerHTML = '';
      this.classList = {
        contains: name => String(this.className).split(/\s+/).includes(name),
        add: (...names) => { this.className = [...new Set([...String(this.className).split(/\s+/).filter(Boolean), ...names])].join(' '); },
        remove: (...names) => { this.className = String(this.className).split(/\s+/).filter(name => name && !names.includes(name)).join(' '); },
        toggle: (name, force) => {
          const has = this.classList.contains(name);
          const next = force === undefined ? !has : Boolean(force);
          if (next) this.classList.add(name); else this.classList.remove(name);
          return next;
        },
      };
      this.dataset = new Proxy({}, {
        get: (_target, key) => this.getAttribute(`data-${dashed(key)}`) ?? undefined,
        set: (_target, key, value) => { this.setAttribute(`data-${dashed(key)}`, String(value)); return true; },
      });
    }
    get id() { return this.getAttribute('id') || ''; }
    set id(value) { this.setAttribute('id', value); }
    get className() { return this.getAttribute('class') || ''; }
    set className(value) { this.setAttribute('class', value); }
    get children() { return this.childNodes.filter(child => child.nodeType === 1); }
    get isConnected() { let node = this; while (node) { if (node === this.ownerDocument.body) return true; node = node.parentNode; } return false; }
    get textContent() { return this._textContent || this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this._textContent = String(value ?? ''); this.childNodes = []; }
    get innerHTML() { return this._innerHTML; }
    set innerHTML(value) {
      this._innerHTML = String(value ?? '');
      this.childNodes = [];
      if (this._innerHTML.includes('data-asset-organize-group-new')) {
        const button = this.ownerDocument.createElement('button');
        button.setAttribute('data-asset-organize-group-new', '');
        this.appendChild(button);
      }
    }
    setAttribute(name, value) { this.attributes.set(String(name), String(value)); }
    getAttribute(name) { return this.attributes.get(String(name)) ?? null; }
    hasAttribute(name) { return this.attributes.has(String(name)); }
    removeAttribute(name) { this.attributes.delete(String(name)); }
    append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
    appendChild(node) {
      if (node.nodeType === 11) { [...node.childNodes].forEach(child => this.appendChild(child)); node.childNodes = []; return node; }
      node.parentNode?.removeChild(node);
      node.parentNode = this;
      this.childNodes.push(node);
      return node;
    }
    removeChild(node) { this.childNodes = this.childNodes.filter(child => child !== node); node.parentNode = null; return node; }
    replaceChildren(...nodes) { this.childNodes.forEach(child => { child.parentNode = null; }); this.childNodes = []; this.append(...nodes); }
    remove() { this.parentNode?.removeChild(this); }
    addEventListener(type, handler) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(handler); }
    removeEventListener(type, handler) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== handler)); }
    async dispatch(type, event = {}) {
      const dispatched = {type, target: this, preventDefault() {}, stopPropagation() {}, ...event};
      const results = (this.listeners.get(type) || []).map(handler => handler(dispatched));
      await Promise.all(results.map(result => Promise.resolve(result)));
      return dispatched;
    }
    querySelectorAll(selector) {
      const found = [];
      const visit = node => node.children.forEach(child => { if (matches(child, selector)) found.push(child); visit(child); });
      visit(this);
      return found;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    contains(node) { for (let current = node; current; current = current.parentNode) if (current === this) return true; return false; }
  }
  class TestDocument {
    constructor() {
      this.body = new TestElement('body', this);
      this.activeElement = null;
      this.listeners = new Map();
    }
    createElement(tagName) { return new TestElement(tagName, this); }
    createDocumentFragment() { return new TestElement('#fragment', this, 11); }
    getElementById(id) { return this.querySelectorAll(`#${id}`)[0] || null; }
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); }
    addEventListener(type, handler) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(handler); }
    removeEventListener(type, handler) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== handler)); }
  }

  const document = new TestDocument();
  const categories = document.createElement('nav');
  categories.id = 'assetCategories';
  const frame = document.createElement('iframe');
  frame.id = 'assetVaultFrame';
  frame.contentDocument = null;
  frame.contentWindow = {GWAssetOrganizeBridge: null};
  const status = document.createElement('div');
  status.id = 'assetVaultStatus';
  document.body.append(categories, frame, status);
  const storage = new Map();
  const localStorage = {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value))};
  const window = {
    innerWidth: 1200,
    innerHeight: 800,
    localStorage,
    lucide: {createIcons() {}},
    addEventListener() {},
    dispatchEvent() {},
  };
  const location = {href: 'http://127.0.0.1/static/assets.html', origin: 'http://127.0.0.1', pathname: '/static/assets.html', search: '', hash: ''};
  const context = createContext({
    window, document, location, localStorage,
    history: {replaceState() {}},
    URL, URLSearchParams,
    CSS: {escape: value => value},
    setTimeout: () => 0,
    clearTimeout() {},
    console,
  });
  window.window = window;
  runInContext(controller, context);

  const calls = [];
  let permitted = true;
  let statusValue = {enabled: true, ready: true};
  let statusReads = 0;
  frame.contentWindow.GWAssetOrganizeBridge = {
    canOrganize: () => true,
    canManageResourceFolders: () => true,
    canRunShotVectorJob: key => permitted && key === 'mine:root:root_0123456789abcdef01234567',
    getShotVectorStatus: async () => { statusReads += 1; return statusValue; },
    runShotVectorJob: async (...args) => { calls.push(args); return {status: 'succeeded', result: {assets: 1, shots: 2, frames: 3}}; },
    executeResourceFolderAction: async () => {},
  };
  context.window.GWAssetTreeHost.onTreeSnapshot({roots: [{
    id: 'root_0123456789abcdef01234567', rootId: 'root_0123456789abcdef01234567', name: 'Film',
    expanded: true, children: [{key: 'mine:folder:folder_123', name: 'Subfolder'}],
  }], boards: [], selected_key: ''});

  const root = categories.querySelector('[data-asset-tree-entry="mine:root:root_0123456789abcdef01234567"]');
  assert.ok(root, 'full controller renders the mounted root from the tree snapshot');
  root.dispatch('contextmenu', {clientX: 20, clientY: 30});
  const rootMenu = document.getElementById('assetOrganizeMenu');
  const vectors = rootMenu?.querySelector('[data-asset-organize-shot-vectors]');
  assert.ok(vectors, 'actual shell context-menu event opens the vector action');
  assert.equal(vectors.disabled, true, 'action starts disabled while local readiness is checked');
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(vectors.disabled, false, 'enabled setting and ready local model enable the action');
  await vectors.dispatch('click');
  assert.deepEqual(calls, [['root_0123456789abcdef01234567', 'Film']]);
  assert.match(document.getElementById('assetOrganizeToast')?.innerHTML || '', /补跑完成/);

  const child = categories.querySelector('[data-asset-tree-entry="mine:folder:folder_123"]');
  assert.ok(child, 'full controller renders the nested folder');
  await child.dispatch('contextmenu');
  assert.equal(document.getElementById('assetOrganizeMenu')?.querySelector('[data-asset-organize-shot-vectors]'), null,
    'nested folder menu does not offer root-only vector rerun');

  permitted = false;
  await root.dispatch('contextmenu');
  const readOnlyVector = document.getElementById('assetOrganizeMenu')?.querySelector('[data-asset-organize-shot-vectors]');
  assert.ok(readOnlyVector?.disabled, 'roots without bridge permission cannot run the action');
  assert.equal(statusReads, 1, 'status is not queried when the bridge denies permission');

  permitted = true;
  statusValue = {enabled: true, ready: false, message: '本地视觉模型或索引尚未就绪'};
  await root.dispatch('contextmenu');
  const notReadyVector = document.getElementById('assetOrganizeMenu')?.querySelector('[data-asset-organize-shot-vectors]');
  await Promise.resolve();
  await Promise.resolve();
  assert.ok(notReadyVector?.disabled, 'missing local model or index keeps the action disabled');
  assert.match(notReadyVector?.title || '', /尚未就绪/);
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
