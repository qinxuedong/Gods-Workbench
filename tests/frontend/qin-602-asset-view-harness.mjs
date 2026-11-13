/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * QIN-602 测试宿主：真实产品模块的最小运行环境。
 *
 * 按 GW-DoD-v1 §4.2，测试不得用源码切片 / vm / 函数替身验证行为。本模块把整棵
 * `web/js` 复制到临时目录（只补 `{"type":"module"}`），用原生 `import()` 真实加载
 * 主模块与延后域，再由用例通过 `window.GWAssetTreeBridge.activate(...)` / 真实
 * `render()` 驱动切换；DOM 由文件内的最小宿主提供（与 #697 r4 已接受的做法一致）。
 *
 * 延迟与失败只注入在真实的模块加载边界（loader 项或模块文件本身的顶层 await /
 * 顶层 throw）：域模块仍然真实 import，不替换成替身。
 */

import {test, after} from 'node:test';
import assert from 'node:assert/strict';
import {
    readFileSync, writeFileSync, cpSync, mkdtempSync, realpathSync, rmSync,
} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const MAIN_REL = 'web/js/modules/asset-manager.js';
const MAIN_SOURCE = readFileSync(path.join(ROOT, MAIN_REL), 'utf8');

/* ------------------------------------------------------------------------- *
 * 最小 DOM 宿主：解析 innerHTML、按选择器查询、记录属性与文本。
 * 只实现产品代码在这条链路上真正用到的能力；遇到未实现的选择器直接抛错，
 * 避免「静默匹配不到」把行为断言变成假绿。
 * ------------------------------------------------------------------------- */

const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'source', 'track', 'wbr']);

class ClassList {
    constructor(element) { this.element = element; }
    get _set() {
        return new Set(String(this.element.getAttribute('class') || '').split(/\s+/).filter(Boolean));
    }
    _write(set) { this.element.setAttribute('class', [...set].join(' ')); }
    add(...names) { const set = this._set; names.forEach(name => set.add(name)); this._write(set); }
    remove(...names) { const set = this._set; names.forEach(name => set.delete(name)); this._write(set); }
    contains(name) { return this._set.has(name); }
    toggle(name, force) {
        const set = this._set;
        const has = set.has(name);
        const next = force === undefined ? !has : Boolean(force);
        if (next) set.add(name); else set.delete(name);
        this._write(set);
        return next;
    }
    item(index) { return [...this._set][index] ?? null; }
    get length() { return this._set.size; }
    toString() { return [...this._set].join(' '); }
}

let NODE_ID = 0;

class StubNode {
    constructor(ownerDocument, tagName) {
        this.ownerDocument = ownerDocument;
        this.nodeType = 1;
        this.tagName = String(tagName || 'div').toUpperCase();
        this.localName = this.tagName.toLowerCase();
        this.childNodes = [];
        this.parentNode = null;
        this.attributes = new Map();
        this.style = {
            setProperty() {},
            removeProperty() {},
            getPropertyValue() { return ''; },
        };
        this.dataset = new Proxy({}, {
            get: (_t, key) => this.getAttribute(`data-${camelToDash(String(key))}`) ?? undefined,
            set: (_t, key, value) => { this.setAttribute(`data-${camelToDash(String(key))}`, String(value)); return true; },
            has: (_t, key) => this.hasAttribute(`data-${camelToDash(String(key))}`),
        });
        this._listeners = new Map();
        this._uid = (NODE_ID += 1);
        this.hidden = false;
        this.value = '';
        this.checked = false;
        this.scrollTop = 0;
        this.scrollLeft = 0;
        this.scrollHeight = 0;
        this.scrollWidth = 0;
        this.clientWidth = 0;
        this.clientHeight = 0;
        this.offsetWidth = 0;
        this.offsetHeight = 0;
        this.classList = new ClassList(this);
    }

    get id() { return this.getAttribute('id') || ''; }
    set id(value) { this.setAttribute('id', value); }
    get className() { return this.getAttribute('class') || ''; }
    set className(value) { this.setAttribute('class', String(value ?? '')); }
    get children() { return this.childNodes.filter(node => node.nodeType === 1); }
    get firstElementChild() { return this.children[0] ?? null; }
    get lastElementChild() { const kids = this.children; return kids[kids.length - 1] ?? null; }
    get nextElementSibling() {
        if (!this.parentNode) return null;
        const kids = this.parentNode.children;
        return kids[kids.indexOf(this) + 1] ?? null;
    }
    get previousElementSibling() {
        if (!this.parentNode) return null;
        const kids = this.parentNode.children;
        return kids[kids.indexOf(this) - 1] ?? null;
    }
    get isConnected() { return true; }

    setAttribute(name, value) { this.attributes.set(String(name).toLowerCase(), String(value)); }
    getAttribute(name) {
        const key = String(name).toLowerCase();
        return this.attributes.has(key) ? this.attributes.get(key) : null;
    }
    hasAttribute(name) { return this.attributes.has(String(name).toLowerCase()); }
    removeAttribute(name) { this.attributes.delete(String(name).toLowerCase()); }
    toggleAttribute(name, force) {
        const next = force === undefined ? !this.hasAttribute(name) : Boolean(force);
        if (next) this.setAttribute(name, ''); else this.removeAttribute(name);
        return next;
    }

    get textContent() {
        return this.childNodes.map(node => node.textContent).join('');
    }
    set textContent(value) {
        this.childNodes = [];
        if (value === '' || value === null || value === undefined) return;
        this.childNodes.push({nodeType: 3, textContent: String(value), parentNode: this});
    }

    get innerHTML() { return this.childNodes.map(serializeNode).join(''); }
    set innerHTML(html) {
        this.childNodes = [];
        for (const node of parseHTML(String(html ?? ''), this.ownerDocument, this)) {
            node.parentNode = this;
            this.childNodes.push(node);
        }
    }
    get outerHTML() { return serializeNode(this); }
    set outerHTML(html) {
        // 【QIN-602 r3】facets 到达后会做概览节点的局部 outerHTML 替换；宿主必须真替换，
        // 不能静默吞掉赋值（否则「标记是否随替换保留」这类断言会假绿）。
        assert.ok(this.parentNode, 'outerHTML 赋值需要父节点');
        const parent = this.parentNode;
        const index = parent.childNodes.indexOf(this);
        const nodes = parseHTML(String(html ?? ''), this.ownerDocument, parent);
        nodes.forEach(node => { node.parentNode = parent; });
        parent.childNodes.splice(index, 1, ...nodes);
        this.parentNode = null;
    }

    appendChild(node) {
        node.parentNode = this;
        this.childNodes.push(node);
        return node;
    }
    insertBefore(node, reference) {
        const index = reference ? this.childNodes.indexOf(reference) : this.childNodes.length;
        node.parentNode = this;
        this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, node);
        return node;
    }
    removeChild(node) {
        const index = this.childNodes.indexOf(node);
        if (index >= 0) this.childNodes.splice(index, 1);
        node.parentNode = null;
        return node;
    }
    remove() { this.parentNode?.removeChild(this); }
    replaceChildren(...nodes) {
        this.childNodes = [];
        nodes.forEach(node => this.appendChild(node));
    }
    insertAdjacentHTML(position, html) {
        const parsed = parseHTML(String(html ?? ''), this.ownerDocument, this);
        const where = String(position).toLowerCase();
        if (where === 'afterbegin') {
            parsed.forEach(node => { node.parentNode = this; });
            this.childNodes.unshift(...parsed);
            return;
        }
        if (where === 'beforeend') {
            parsed.forEach(node => { node.parentNode = this; });
            this.childNodes.push(...parsed);
            return;
        }
        assert.ok(this.parentNode, `insertAdjacentHTML: ${position} 需要父节点`);
        if (where === 'beforebegin') this.parentNode.insertBefore(parsed[0], this);
        else if (where === 'afterend') this.parentNode.insertBefore(parsed[0], this.nextElementSibling);
        else throw new Error(`insertAdjacentHTML: 未支持的位置 ${position}`);
    }

    matches(selector) { return matchesSelector(this, selector); }
    closest(selector) {
        let node = this;
        while (node && node.nodeType === 1) {
            if (matchesSelector(node, selector)) return node;
            node = node.parentNode;
        }
        return null;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
    querySelectorAll(selector) {
        const groups = parseSelector(selector);
        const found = [];
        const walk = node => {
            for (const child of node.children) {
                if (groups.some(group => matchesGroup(child, group))) found.push(child);
                walk(child);
            }
        };
        walk(this);
        return found;
    }
    getElementsByClassName(name) {
        return this.querySelectorAll(`.${String(name).split(/\s+/).join('.')}`);
    }
    getElementsByTagName(name) {
        const tag = String(name).toUpperCase();
        const found = [];
        const walk = node => {
            for (const child of node.children) {
                if (tag === '*' || child.tagName === tag) found.push(child);
                walk(child);
            }
        };
        walk(this);
        return found;
    }

    addEventListener(type, handler) {
        if (!this._listeners.has(type)) this._listeners.set(type, new Set());
        this._listeners.get(type).add(handler);
    }
    removeEventListener(type, handler) { this._listeners.get(type)?.delete(handler); }
    dispatchEvent(event) {
        for (const handler of this._listeners.get(event?.type) || []) handler.call(this, event);
        return true;
    }

    getBoundingClientRect() {
        return {top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};
    }
    getContext() { return null; }
    focus() {}
    blur() {}
    click() {}
    select() {}
    setSelectionRange() {}
    scrollIntoView() {}
    scrollTo() {}
    animate() { return {finished: Promise.resolve(), cancel() {}}; }
    cloneNode() { return this; }
    toDataURL() { return 'data:,'; }
}

function camelToDash(value) {
    return value.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);
}

function serializeNode(node) {
    if (node.nodeType === 3) return node.textContent;
    const attrs = [...node.attributes.entries()]
        .map(([name, value]) => (value === '' ? ` ${name}` : ` ${name}="${String(value).replace(/"/g, '&quot;')}"`))
        .join('');
    if (VOID_TAGS.has(node.localName)) return `<${node.localName}${attrs}>`;
    return `<${node.localName}${attrs}>${node.childNodes.map(serializeNode).join('')}</${node.localName}>`;
}

const RAW_TEXT_TAGS = new Set(['script', 'style']);

function parseHTML(html, ownerDocument, parentHint) {
    const nodes = [];
    const stack = [];
    let index = 0;
    const push = node => {
        const parent = stack[stack.length - 1];
        node.parentNode = parent || parentHint || null;
        if (parent) parent.childNodes.push(node);
        else nodes.push(node);
    };
    while (index < html.length) {
        const lt = html.indexOf('<', index);
        if (lt < 0) {
            const text = html.slice(index);
            if (text) push({nodeType: 3, textContent: text, parentNode: null});
            break;
        }
        if (lt > index) push({nodeType: 3, textContent: html.slice(index, lt), parentNode: null});
        if (html.startsWith('<!--', lt)) {
            const end = html.indexOf('-->', lt);
            index = end < 0 ? html.length : end + 3;
            continue;
        }
        if (html.startsWith('<!', lt)) {
            const end = html.indexOf('>', lt);
            index = end < 0 ? html.length : end + 1;
            continue;
        }
        const closing = html[lt + 1] === '/';
        let cursor = lt + (closing ? 2 : 1);
        while (cursor < html.length && /[a-zA-Z0-9:_-]/.test(html[cursor])) cursor += 1;
        const name = html.slice(lt + (closing ? 2 : 1), cursor).toLowerCase();
        if (!name) {
            push({nodeType: 3, textContent: '<', parentNode: null});
            index = lt + 1;
            continue;
        }
        const tagEnd = findTagEnd(html, lt);
        if (closing) {
            for (let depth = stack.length - 1; depth >= 0; depth -= 1) {
                if (stack[depth].localName === name) { stack.length = depth; break; }
            }
            index = tagEnd + 1;
            continue;
        }
        const element = new StubNode(ownerDocument, name);
        const attrText = html.slice(cursor, tagEnd);
        for (const match of attrText.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g)) {
            const attrName = match[1];
            if (!attrName) continue;
            element.setAttribute(attrName, match[2] ?? match[3] ?? match[4] ?? '');
        }
        push(element);
        index = tagEnd + 1;
        if (VOID_TAGS.has(name) || /\/\s*$/.test(html.slice(lt, tagEnd))) continue;
        if (RAW_TEXT_TAGS.has(name)) {
            const closeAt = html.toLowerCase().indexOf(`</${name}`, index);
            const end = closeAt < 0 ? html.length : closeAt;
            if (end > index) element.childNodes.push({nodeType: 3, textContent: html.slice(index, end), parentNode: element});
            const after = closeAt < 0 ? html.length : html.indexOf('>', closeAt) + 1;
            index = after <= 0 ? html.length : after;
            continue;
        }
        stack.push(element);
    }
    return nodes;
}

function findTagEnd(html, start) {
    let quote = '';
    for (let i = start + 1; i < html.length; i += 1) {
        const ch = html[i];
        if (quote) {
            if (ch === quote) quote = '';
            continue;
        }
        if (ch === '"' || ch === "'") { quote = ch; continue; }
        if (ch === '>') return i;
    }
    return html.length - 1;
}

function parseSelector(selector) {
    return String(selector).split(',').map(part => {
        const steps = [];
        let rest = part.trim();
        let combinator = ' ';
        while (rest) {
            const leading = /^[>+]?\s*/.exec(rest)[0];
            const sign = leading.trim();
            if (sign === '>') combinator = '>';
            rest = rest.slice(leading.length);
            if (!rest) break;
            const compoundText = /^[^\s>]+/.exec(rest)[0];
            steps.push({combinator: steps.length ? combinator : ' ', compound: parseCompound(compoundText)});
            rest = rest.slice(compoundText.length);
            combinator = ' ';
        }
        return steps;
    });
}

function parseCompound(text) {
    const compound = {tag: '', id: '', classes: [], attrs: [], pseudos: []};
    const tokens = text.match(/(\[[^\]]*\]|:[\w-]+(?:\([^)]*\))?|[.#]?[\w*-]+|\*)/g) || [];
    for (const token of tokens) {
        if (token === '*') continue;
        if (token.startsWith('.')) compound.classes.push(token.slice(1));
        else if (token.startsWith('#')) compound.id = token.slice(1);
        else if (token.startsWith('[')) {
            const match = /^\[([\w:-]+)(?:([~^$*|]?=)\s*"?([^"\]]*)"?)?\]$/.exec(token);
            assert.ok(match, `无法解析属性选择器 ${token}`);
            compound.attrs.push({name: match[1].toLowerCase(), op: match[2] || '', value: match[3] ?? ''});
        } else if (token.startsWith(':')) {
            const match = /^:([\w-]+)(?:\((.*)\))?$/.exec(token);
            assert.ok(match, `无法解析伪类 ${token}`);
            compound.pseudos.push({name: match[1], arg: match[2]});
        } else compound.tag = token.toUpperCase();
    }
    return compound;
}

function matchesCompound(element, compound) {
    if (compound.tag && element.tagName !== compound.tag) return false;
    if (compound.id && element.id !== compound.id) return false;
    const classes = String(element.getAttribute('class') || '').split(/\s+/);
    for (const cls of compound.classes) if (!classes.includes(cls)) return false;
    for (const attr of compound.attrs) {
        const actual = element.getAttribute(attr.name);
        if (actual === null) return false;
        if (!attr.op) continue;
        if (attr.op === '=' && actual !== attr.value) return false;
        if (attr.op === '^=' && !actual.startsWith(attr.value)) return false;
        if (attr.op === '$=' && !actual.endsWith(attr.value)) return false;
        if (attr.op === '*=' && !actual.includes(attr.value)) return false;
    }
    for (const pseudo of compound.pseudos) {
        if (pseudo.name === 'not') {
            if (matchesCompound(element, parseCompound(pseudo.arg))) return false;
        } else if (pseudo.name === 'first-child') {
            if (element.parentNode?.children[0] !== element) return false;
        } else if (pseudo.name === 'last-child') {
            const kids = element.parentNode?.children || [];
            if (kids[kids.length - 1] !== element) return false;
        } else if (pseudo.name === 'disabled') {
            if (!element.hasAttribute('disabled')) return false;
        } else if (pseudo.name === 'enabled') {
            if (element.hasAttribute('disabled')) return false;
        } else if (pseudo.name === 'checked') {
            if (!element.checked) return false;
        } else if (['popover-open', 'hover', 'active', 'focus', 'focus-visible', 'focus-within', 'visited'].includes(pseudo.name)) {
            // 状态伪类在无头宿主人没有交互历史：一律「未命中」，与浏览器初始状态一致。
            return false;
        } else {
            throw new Error(`测试宿主未实现伪类 :${pseudo.name}`);
        }
    }
    return true;
}

function matchesGroup(element, steps) {
    const match = (node, index) => {
        if (!matchesCompound(node, steps[index].compound)) return false;
        if (index === 0) return true;
        if (steps[index].combinator === '>') {
            const parent = node.parentNode;
            return Boolean(parent && parent.nodeType === 1 && match(parent, index - 1));
        }
        let ancestor = node.parentNode;
        while (ancestor && ancestor.nodeType === 1) {
            if (match(ancestor, index - 1)) return true;
            ancestor = ancestor.parentNode;
        }
        return false;
    };
    return match(element, steps.length - 1);
}

function matchesSelector(element, selector) {
    if (!element || element.nodeType !== 1) return false;
    return parseSelector(selector).some(group => matchesGroup(element, group));
}

class StubDocument {
    constructor() {
        this.readyState = 'complete';
        this._listeners = new Map();
        this.nodeType = 9;
        this.documentElement = new StubNode(this, 'html');
        this.head = new StubNode(this, 'head');
        this.body = new StubNode(this, 'body');
        this.documentElement.childNodes.push(this.head, this.body);
        this.head.parentNode = this.documentElement;
        this.body.parentNode = this.documentElement;
        this.activeElement = null;
        this.cookie = '';
        this.visibilityState = 'visible';
        this.hidden = false;
        this.fonts = {ready: Promise.resolve(), addEventListener() {}};
        this.adoptedStyleSheets = [];
        this._byId = new Map();
    }

    createElement(tag) { return new StubNode(this, tag); }
    createTextNode(text) { return {nodeType: 3, textContent: String(text), parentNode: null}; }
    createDocumentFragment() { return new StubNode(this, 'fragment'); }
    getElementById(id) {
        const key = String(id);
        if (!this._byId.has(key)) {
            const node = this.createElement('div');
            node.setAttribute('id', key);
            this.body.appendChild(node);
            this._byId.set(key, node);
        }
        return this._byId.get(key);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
    querySelectorAll(selector) {
        const found = this.documentElement.querySelectorAll(selector);
        return matchesSelector(this.documentElement, selector) ? [this.documentElement, ...found] : found;
    }
    addEventListener(type, handler) {
        if (!this._listeners.has(type)) this._listeners.set(type, new Set());
        this._listeners.get(type).add(handler);
    }
    removeEventListener(type, handler) { this._listeners.get(type)?.delete(handler); }
    dispatchEvent(event) {
        for (const handler of [...(this._listeners.get(event?.type) || [])]) handler.call(this, event);
        return true;
    }
    contains() { return true; }
    execCommand() { return true; }
}

/* ------------------------------------------------------------------------- *
 * 宿主安装 / 模块树 / 加载器注入
 * ------------------------------------------------------------------------- */

const TREES = [];
let uninstallHost = null;

function makeProductTree(loaderPatches = {}, sourceEdits = [], fileEdits = []) {
    const root = realpathSync(mkdtempSync(path.join(os.tmpdir(), 'gw-qin602-esm-')));
    cpSync(path.join(ROOT, 'web', 'js'), path.join(root, 'web', 'js'), {recursive: true});
    writeFileSync(path.join(root, 'package.json'), '{"type": "module"}\n', 'utf8');
    const mainFile = path.join(root, MAIN_REL);
    let source = readFileSync(mainFile, 'utf8');
    for (const [domain, replacement] of Object.entries(loaderPatches)) {
        const pattern = new RegExp(`'${domain}'\\s*:\\s*\\(\\)\\s*=>\\s*import\\('\\./asset-manager/${domain}\\.js'\\)`);
        assert.ok(pattern.test(source), `主文件里找不到 ${domain} 域的 loader 定义`);
        source = source.replace(pattern, `'${domain}': ${replacement}`);
    }
    for (const edit of sourceEdits) {
        assert.ok(source.includes(edit.from), `反证补丁锚点不存在：${edit.from}`);
        source = source.replace(edit.from, edit.to);
    }
    writeFileSync(mainFile, source, 'utf8');
    for (const edit of fileEdits) {
        const target = path.join(root, edit.file);
        const text = readFileSync(target, 'utf8');
        writeFileSync(target, edit.prepend ? edit.prepend + text : `${text}\n${edit.append}`, 'utf8');
    }
    TREES.push(root);
    return root;
}

function dropTree(root) {
    const index = TREES.indexOf(root);
    if (index >= 0) TREES.splice(index, 1);
    try { rmSync(root, {recursive: true, force: true}); } catch (_) { /* Windows 句柄释放延迟 */ }
}

after(() => {
    for (const root of TREES.splice(0)) {
        try { rmSync(root, {recursive: true, force: true}); } catch (_) {}
    }
    if (uninstallHost) { uninstallHost(); uninstallHost = null; }
});

const MOUNTED_BOARD = {
    customBoards: [{id: 'library_demo', name: '挂载文件夹A', preset: 'all'}],
    order: ['registry', 'library_demo'],
    hidden: [],
};

function installHost({embedded = true, theme = 'dark', boards = MOUNTED_BOARD, fetchImpl = null} = {}) {
    if (uninstallHost) { uninstallHost(); uninstallHost = null; }
    NODE_ID = 0;
    const saved = new Map();
    // 【QIN-602 r3】受控网络：用例可注入真实 fetch 响应并读取请求台账，用来断言
    // 「点收集箱」这类入口确实发出产品副作用请求（而不是只切了个 activeTab）。
    const requests = [];
    const put = (key, value) => {
        saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, {configurable: true, writable: true, value});
    };
    const document = new StubDocument();
    if (embedded) document.documentElement.setAttribute('class', 'workspace-embedded vault-shell-embedded');
    if (theme) {
        document.documentElement.setAttribute('data-theme', theme);
        document.documentElement.classList.add(`studio-theme-${theme}`, `theme-${theme}`);
    }
    document.body.setAttribute('class', 'gw-signal gw-page--asset-manager gw-type-body-md');

    const storage = new Map();
    if (boards) storage.set('asset_board_navigation_v1', JSON.stringify(boards));
    const storageApi = {
        getItem: key => (storage.has(String(key)) ? storage.get(String(key)) : null),
        setItem: (key, value) => { storage.set(String(key), String(value)); },
        removeItem: key => { storage.delete(String(key)); },
        clear: () => storage.clear(),
        key: index => [...storage.keys()][index] ?? null,
        get length() { return storage.size; },
    };

    put('document', document);
    put('window', globalThis);
    put('parent', globalThis);
    put('top', globalThis);
    put('self', globalThis);
    put('localStorage', storageApi);
    put('sessionStorage', {getItem: () => null, setItem() {}, removeItem() {}, clear() {}, length: 0});
    put('location', {
        search: '?embedded=1&vault=1',
        href: 'http://127.0.0.1:2077/static/asset-manager.html?embedded=1&vault=1',
        pathname: '/static/asset-manager.html',
        origin: 'http://127.0.0.1:2077',
        hash: '',
        host: '127.0.0.1:2077',
        protocol: 'http:',
        assign() {}, replace() {}, reload() {},
    });
    put('matchMedia', () => ({matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}}));
    put('getComputedStyle', () => ({getPropertyValue() { return ''; }}));
    put('requestAnimationFrame', callback => setTimeout(() => callback(Date.now()), 0));
    put('cancelAnimationFrame', id => clearTimeout(id));
    // 空闲回调在本宿主不主动触发：收集箱/治理的投递请求因此不会抢在用例驱动之前
    // 把目标域提前加载好，用例才能精确控制每个域的落地时机。
    put('requestIdleCallback', () => 0);
    put('cancelIdleCallback', () => {});
    put('navigator', {userAgent: 'node', clipboard: {writeText: async () => {}, readText: async () => ''}});
    put('addEventListener', (type, handler) => document.addEventListener(type, handler));
    put('removeEventListener', (type, handler) => document.removeEventListener(type, handler));
    put('dispatchEvent', event => document.dispatchEvent(event));
    put('isSecureContext', false);
    put('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} });
    put('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    put('MutationObserver', class { observe() {} disconnect() {} takeRecords() { return []; } });
    put('Event', class { constructor(type, init) { this.type = type; Object.assign(this, init); } });
    put('CustomEvent', class { constructor(type, init) { this.type = type; Object.assign(this, init); } });
    put('DOMParser', class { parseFromString() { return document; } });
    // 本票验收渲染编排，不验接口数据：所有网络请求一律失败，避免真实出网。
    // 收集箱接口给「一直没回」的响应：其余接口一律失败（首屏走失败兜底、闸门照常放行）。
    // 不能用立即 reject —— 收集箱在失败后会「请求→重绘→再请求」，本票不验收该既有缺陷，
    // 立即失败会让它在本宿主里以微任务速度空转。
    // 注入 fetchImpl 时由用例完全接管响应，但请求台账始终记录（观测不替代行为）。
    const defaultFetch = url => (String(url).includes('/api/asset-inbox')
        ? new Promise(() => {})
        : Promise.reject(new Error('QIN-602 测试宿主不提供网络')));
    put('fetch', (url, init) => {
        const entry = {url: String(url), method: String(init?.method || 'GET').toUpperCase()};
        requests.push(entry);
        return fetchImpl ? fetchImpl(url, init, entry) : defaultFetch(url, init);
    });

    uninstallHost = () => {
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    };
    return {document, root: document.getElementById('assetManagerRoot'), storage: storageApi, requests};
}

async function loadProduct(treeRoot) {
    const url = pathToFileURL(path.join(treeRoot, MAIN_REL)).href;
    return import(url);
}

/** 走产品自己的 DOMContentLoaded 引导段，放开首屏延后域网关。 */
async function bootProduct(document) {
    document.dispatchEvent({type: 'DOMContentLoaded'});
    await new Promise(resolve => setTimeout(resolve, 80));
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

/**
 * 【QIN-602 r3】捕获整段窗口内的 console.error：域模块失败可能发生在 bootProduct、
 * 延后域队列或几百毫秒后的迟到代际里，只包住「点击那一下」会漏掉真正的失败日志。
 * 用例应按具体前缀筛选，避免被依赖链的 `gwCall 失败: …` 噪声误命中。
 */
export function captureConsoleErrors() {
    const logs = [];
    const original = console.error;
    console.error = (...args) => { logs.push(args.map(String).join(' ')); };
    return {
        logs,
        withPrefix(prefix) { return logs.filter(line => line.startsWith(prefix)); },
        stop() { console.error = original; },
    };
}

/**
 * 【QIN-602 r3】收集箱真实副作用链路所需的受控响应：列表返回未读数 3，
 * openAssetInbox() 才会走 markAssetInboxViewed() 发 POST /api/asset-inbox/viewed。
 * 其余端点一律拒绝，避免真实出网。
 */
export function inboxSideEffectFetch(url) {
    const target = String(url);
    const response = data => ({status: 200, ok: true, json: async () => data});
    if (target.includes('/api/asset-inbox/viewed')) return Promise.resolve(response({ok: true}));
    if (target.includes('/api/asset-inbox?limit=500')) {
        return Promise.resolve(response({items: [], settings: {unread_count: 3, is_default: true}}));
    }
    return Promise.reject(new Error('QIN-602 测试宿主不提供网络'));
}

function topbar(root) {
    return {
        overview: root.querySelectorAll('.registry-overview-shell').length,
        toolbar: root.querySelectorAll('.content-toolbar.registry-toolbar').length,
        marked: root.querySelectorAll('[data-asset-view-topbar]').length,
    };
}

function toolbarTitle(root) {
    const toolbar = root.querySelector('.content-toolbar.registry-toolbar');
    return toolbar?.querySelector('.content-heading strong')?.textContent?.trim() ?? null;
}


/**
 * 建一个已启动的真实产品模块宿主。
 * @param {object} options
 * @param {boolean} [options.embedded] 是否嵌入态（决定是否渲染顶栏）
 * @param {string} [options.tab] 启动后的 activeTab；传 null 跳过首次渲染
 * @param {object} [options.boards] 真实看板状态（写入 asset_board_navigation_v1）
 * @param {object} [options.loaderPatches] 域加载器改写（仅用于延迟/失败注入）
 * @param {Array} [options.sourceEdits] 主文件反证补丁
 * @param {Array} [options.fileEdits] 域模块文件级注入（顶层 throw / 顶层 await）
 */
export async function createAssetViewHost({
    embedded = true, theme = 'dark', tab = 'registry', boards = MOUNTED_BOARD,
    loaderPatches = {}, sourceEdits = [], fileEdits = [], fetchImpl = null,
} = {}) {
    const host = installHost({embedded, theme, boards, fetchImpl});
    const tree = makeProductTree(loaderPatches, sourceEdits, fileEdits);
    const main = await loadProduct(tree);
    await bootProduct(host.document);
    if (tab) {
        main.setActiveTab(tab);
        main.render();
        await wait(60);
    }
    return {
        main,
        tree,
        document: host.document,
        root: host.root,
        storage: host.storage,
        /** 真实网络请求台账：{url, method}[]，用于断言入口的产品副作用 */
        requests: host.requests,
        /** 切到某个 tab 并真实渲染一帧 */
        async setTab(next) {
            main.setActiveTab(next);
            main.render();
            await wait(60);
        },
        /** 真实执行工具栏渲染函数（用于 hideHeading 这类渲染契约断言） */
        toolbar(nextTab, options = {}) {
            main.setActiveTab(nextTab);
            return main.renderRegistryToolbar(null, `标题-${nextTab}`, `副标题-${nextTab}`, {allowUrl: true, ...options});
        },
        dispose() { dropTree(tree); },
    };
}

export {
    MAIN_SOURCE,
    MAIN_REL,
    MOUNTED_BOARD,
    bootProduct,
    dropTree,
    installHost,
    loadProduct,
    makeProductTree,
    toolbarTitle,
    topbar,
    wait,
};
