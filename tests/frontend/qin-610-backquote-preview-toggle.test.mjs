import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import {pathToFileURL} from 'node:url';
import {createAssetViewHost, wait} from './qin-602-asset-view-harness.mjs';

function backquote(target, extra = {}){
    return {
        type:'keydown', key:'`', code:'Backquote', target,
        repeat:false, ctrlKey:false, metaKey:false, altKey:false, isComposing:false,
        defaultPrevented:false, prevented:false, stopped:false,
        preventDefault(){ this.defaultPrevented = true; this.prevented = true; },
        stopPropagation(){ this.stopped = true; },
        ...extra,
    };
}

function keyEvent(key, code, target, extra = {}){
    return {
        type:'keydown', key, code, target,
        repeat:false, ctrlKey:false, metaKey:false, altKey:false, isComposing:false,
        defaultPrevented:false, prevented:false, stopped:false,
        preventDefault(){ this.defaultPrevented = true; this.prevented = true; },
        stopPropagation(){ this.stopped = true; },
        ...extra,
    };
}

// 确保测试宿主启动期间注册的定时器不会阻止 Node 测试进程在用例结束后退出。
function trackUnrefTimers(){
    const prevSetInterval = globalThis.setInterval;
    const prevSetTimeout = globalThis.setTimeout;
    const intervals = new Set();
    const timeouts = new Set();
    globalThis.setInterval = (...args) => {
        const id = prevSetInterval(...args);
        id?.unref?.();
        intervals.add(id);
        return id;
    };
    globalThis.setTimeout = (...args) => {
        const id = prevSetTimeout(...args);
        timeouts.add(id);
        return id;
    };
    return () => {
        for(const id of intervals) clearInterval(id);
        for(const id of timeouts) clearTimeout(id);
        globalThis.setInterval = prevSetInterval;
        globalThis.setTimeout = prevSetTimeout;
    };
}

function installCanvasAndImageStubs(doc){
    const prevImage = globalThis.Image;
    globalThis.Image = class MockImage {
        constructor(){
            this.naturalWidth = 320;
            this.naturalHeight = 180;
            this.width = 320;
            this.height = 180;
        }
        set src(val){
            this._src = val;
            queueMicrotask(() => this.onload?.());
        }
        get src(){ return this._src || ''; }
    };
    const StubProto = Object.getPrototypeOf(doc.body);
    const origQsa = StubProto.querySelectorAll;
    const origQs = StubProto.querySelector;
    const origAppend = StubProto.append;
    const origReplaceWith = StubProto.replaceWith;
    const origGetContext = StubProto.getContext;
    const normalizeSel = sel => String(sel).replace(/:scope\s*>\s*/g, '').replace(/\s*>\s*/g, '>');
    StubProto.querySelectorAll = function(sel){
        return origQsa.call(this, normalizeSel(sel));
    };
    StubProto.querySelector = function(sel){
        return origQs.call(this, normalizeSel(sel));
    };
    if(typeof StubProto.append !== 'function'){
        StubProto.append = function(...nodes){
            nodes.forEach(n => this.appendChild(typeof n === 'string' ? doc.createTextNode(n) : n));
        };
    }
    if(typeof StubProto.replaceWith !== 'function'){
        StubProto.replaceWith = function(...nodes){
            const parent = this.parentNode;
            if(!parent) return;
            const list = nodes.map(n => (typeof n === 'string' ? doc.createTextNode(n) : n));
            const idx = parent.childNodes.indexOf(this);
            if(idx >= 0){
                this.parentNode = null;
                list.forEach(n => { n.parentNode = parent; });
                parent.childNodes.splice(idx, 1, ...list);
            }
        };
    }
    StubProto.getContext = function(){
        if(this.localName !== 'canvas') return origGetContext ? origGetContext.apply(this, arguments) : null;
        return {
            save(){}, restore(){}, clearRect(){}, fillRect(){}, strokeRect(){},
            beginPath(){}, moveTo(){}, lineTo(){}, stroke(){}, fill(){},
            translate(){}, scale(){}, rotate(){}, drawImage(){}, setTransform(){},
            measureText: text => ({width:String(text || '').length * 8}),
            fillText(){}, setLineDash(){},
        };
    };
    if(typeof StubProto.toDataURL !== 'function'){
        StubProto.toDataURL = () => 'data:image/png;base64,';
    }
    if(typeof doc.createElementNS !== 'function'){
        doc.createElementNS = (_ns, tag) => doc.createElement(tag);
    }
    return () => {
        StubProto.querySelectorAll = origQsa;
        StubProto.querySelector = origQs;
        if(origAppend) StubProto.append = origAppend;
        else delete StubProto.append;
        if(origReplaceWith) StubProto.replaceWith = origReplaceWith;
        else delete StubProto.replaceWith;
        if(origGetContext) StubProto.getContext = origGetContext;
        else delete StubProto.getContext;
        if(prevImage === undefined) delete globalThis.Image;
        else globalThis.Image = prevImage;
    };
}

function registerLocalImage(host, id = 'qin-610-img'){
    const asset = {
        id, name:'QIN-610 shot.png', filename:'qin-610-shot.png',
        path:'shots/qin-610-shot.png',
        kind:'image', mime_type:'image/png', url:'/assets/qin-610-shot.png',
    };
    host.main.setLocalItemMap(new Map([[asset.id, asset]]));
    host.main.setAssetLibrary({libraries:[{id:'test', categories:[{items:[asset]}]}]});
    const card = host.document.createElement('div');
    card.setAttribute('data-detail-source', 'local');
    card.setAttribute('data-detail-id', asset.id);
    card.setAttribute('data-local-card', asset.id);
    host.root.appendChild(card);
    host.document.activeElement = card;
    return {asset, card};
}

function registerLocalText(host, id = 'qin-610-text', content = 'initial text'){
    const asset = {
        id, name:'QIN-610 note.txt', filename:'qin-610-note.txt',
        path:'notes/qin-610-note.txt',
        kind:'text', mime_type:'text/plain', url:'/assets/qin-610-note.txt',
        content, size:content.length,
    };
    host.main.setLocalItemMap(new Map([[asset.id, asset]]));
    host.main.setAssetLibrary({libraries:[{id:'test', categories:[{items:[asset]}]}]});
    const card = host.document.createElement('div');
    card.setAttribute('data-detail-source', 'local');
    card.setAttribute('data-detail-id', asset.id);
    card.setAttribute('data-local-card', asset.id);
    host.root.appendChild(card);
    host.document.activeElement = card;
    return {asset, card};
}

test('AC1：图片资产 500ms 内连续按两次 Backquote 只开关预览，不进入单图编辑器', async () => {
    const restoreTimers = trackUnrefTimers();
    const host = await createAssetViewHost({tab:'local'});
    const restore = installCanvasAndImageStubs(host.document);
    try {
        const {asset, card} = registerLocalImage(host);

        const openEvent = backquote(card);
        host.document.dispatchEvent(openEvent);
        await wait(20);
        assert.equal(openEvent.prevented, true);
        assert.ok(host.document.querySelector('.asset-detail-viewer'), '首次 Backquote 应打开图片预览');
        assert.equal(host.main.detailViewerState.open, true);
        assert.equal(host.main.detailViewerState.id, asset.id);

        // 500ms 内立刻第二次按下 Backquote：必须直接关闭预览，绝不进入单图编辑器
        const closeEvent = backquote(host.document.body);
        host.document.dispatchEvent(closeEvent);
        await wait(30);
        assert.equal(closeEvent.prevented, true);
        assert.equal(host.document.querySelector('.asset-detail-viewer'), null, '第二次 Backquote 应立即关闭预览');
        assert.equal(host.main.detailViewerState.open, false);
        assert.equal(host.main.detailImageEditorState, null, '不得进入单图编辑器');
        assert.equal(host.document.querySelector('[data-image-editor-canvas]'), null);
    } finally {
        restore();
        host.dispose();
        restoreTimers();
    }
});

test('AC2：非按键打开图片预览后，按一次 Backquote 立即关闭预览', async () => {
    const restoreTimers = trackUnrefTimers();
    const host = await createAssetViewHost({tab:'local'});
    const restore = installCanvasAndImageStubs(host.document);
    try {
        const {asset} = registerLocalImage(host);
        await host.main.gwCall('preview', 'showDetailPreview', 'local', asset.id);
        await wait(20);
        assert.equal(host.main.detailViewerState.open, true);
        assert.ok(host.document.querySelector('.asset-detail-viewer'));

        const closeEvent = backquote(host.document.body);
        host.document.dispatchEvent(closeEvent);
        await wait(20);
        assert.equal(closeEvent.prevented, true);
        assert.equal(host.main.detailViewerState.open, false);
        assert.equal(host.document.querySelector('.asset-detail-viewer'), null);
        assert.equal(host.main.detailImageEditorState, null);
    } finally {
        restore();
        host.dispose();
        restoreTimers();
    }
});

test('AC3：单图编辑器中按一次 Backquote 只退出编辑器回到预览，有未保存修改时触发确认提示', async () => {
    const restoreTimers = trackUnrefTimers();
    const host = await createAssetViewHost({tab:'local'});
    const restore = installCanvasAndImageStubs(host.document);
    const prevConfirm = globalThis.window.confirm;
    try {
        const {asset} = registerLocalImage(host);
        await host.main.gwCall('preview', 'showDetailPreview', 'local', asset.id);
        await wait(20);
        await host.main.gwCall('preview', 'openDetailImageEditor', asset.id, {item:asset});
        await wait(30);
        assert.ok(host.main.detailImageEditorState, '应成功打开单图编辑器');

        // 1. 有未保存改动且用户取消确认：保持在编辑器，不关闭
        let confirmCalls = 0;
        globalThis.window.confirm = msg => {
            confirmCalls += 1;
            assert.match(String(msg), /未保存/);
            return false;
        };
        host.main.detailImageEditorState.dirty = true;
        const dirtyCancelEvent = backquote(host.document.body);
        host.document.dispatchEvent(dirtyCancelEvent);
        await wait(20);
        assert.equal(dirtyCancelEvent.prevented, true);
        assert.equal(confirmCalls, 1, '有未保存改动时应触发 confirm 提示');
        assert.equal(host.main.detailImageEditorState, null, '取消保存即放弃修改并退出编辑器回到预览');
        assert.equal(host.main.detailViewerState.open, true, '不得越层关闭详情预览');

        // 2. 无未保存改动：重新进入单图编辑器后按一次 Backquote 直接返回预览
        await host.main.gwCall('preview', 'openDetailImageEditor', asset.id, {item:asset});
        await wait(30);
        assert.ok(host.main.detailImageEditorState);
        host.main.detailImageEditorState.dirty = false;
        confirmCalls = 0;
        const cleanExitEvent = backquote(host.document.body);
        host.document.dispatchEvent(cleanExitEvent);
        await wait(20);
        assert.equal(cleanExitEvent.prevented, true);
        assert.equal(confirmCalls, 0);
        assert.equal(host.main.detailImageEditorState, null, '按一次 Backquote 应退出单图编辑器');
        assert.equal(host.main.detailViewerState.open, true, '仍停留在素材预览，不回到列表');
        assert.ok(host.document.querySelector('.asset-detail-viewer'));
    } finally {
        globalThis.window.confirm = prevConfirm;
        restore();
        host.dispose();
        restoreTimers();
    }
});

test('AC4：可编辑文本预览中，编辑态按一次 Backquote 退出编辑回到预览（有改动自动保存，焦点在非输入区也不越层关闭）', async () => {
    const restoreTimers = trackUnrefTimers();
    const savedBodies = [];
    const host = await createAssetViewHost({
        tab:'local',
        fetchImpl: async (url, init) => {
            const u = String(url);
            const method = String(init?.method || 'GET').toUpperCase();
            if(u.includes('/api/asset-content') && method === 'GET'){
                return {ok:true, status:200, json: async () => ({content:'initial text'})};
            }
            if(u.includes('/api/asset-content') && (method === 'PATCH' || method === 'PUT' || method === 'POST')){
                savedBodies.push(JSON.parse(String(init?.body || '{}')));
                return {ok:true, status:200, json: async () => ({ok:true, size:12, updated_at:Date.now()})};
            }
            if(u.includes('/api/asset-content/versions')){
                return {ok:true, status:200, json: async () => ({versions:[]})};
            }
            return {ok:true, status:200, json: async () => ({})};
        },
    });
    const restore = installCanvasAndImageStubs(host.document);
    try {
        const {asset} = registerLocalText(host);
        await host.main.gwCall('preview', 'showDetailPreview', 'local', asset.id);
        await wait(30);
        host.main.detailViewerState.text = 'initial text';
        host.main.detailViewerState.textLoading = false;
        host.main.detailViewerState.textError = '';
        host.main.detailViewerState.textTruncated = false;

        const clickEdit = () => {
            const btn = host.document.querySelector('[data-detail-text-edit]');
            assert.ok(btn, '应存在编辑正文按钮');
            host.document.dispatchEvent({type:'click', target:btn});
        };

        // 1. 进入正文编辑且无改动：在 textarea 中按一次 Backquote 退出编辑回到预览，不发保存请求
        clickEdit();
        await wait(20);
        assert.equal(host.main.detailViewerState.textEditing, true);
        const editor1 = host.document.querySelector('[data-detail-text-editor]');
        assert.ok(editor1, '应渲染正文编辑框');
        editor1.value = 'initial text';
        const exitCleanEvent = backquote(editor1);
        host.document.dispatchEvent(exitCleanEvent);
        await wait(20);
        assert.equal(exitCleanEvent.prevented, true);
        assert.equal(host.main.detailViewerState.textEditing, false, '无改动按 Backquote 应退出正文编辑');
        assert.equal(host.main.detailViewerState.open, true, '应回到预览，不关闭详情');
        assert.equal(savedBodies.length, 0);

        // 2. 进入正文编辑、焦点移到非输入区（如面板按钮）时按 Backquote：只退编辑、绝不越层关闭预览
        clickEdit();
        await wait(20);
        assert.equal(host.main.detailViewerState.textEditing, true);
        const editor2 = host.document.querySelector('[data-detail-text-editor]');
        editor2.value = 'initial text';
        const panelButton = host.document.createElement('button');
        host.root.appendChild(panelButton);
        const exitBlurEvent = backquote(panelButton);
        host.document.dispatchEvent(exitBlurEvent);
        await wait(20);
        assert.equal(exitBlurEvent.prevented, true);
        assert.equal(host.main.detailViewerState.textEditing, false, '焦点不在输入框时按 Backquote 也应退出正文编辑');
        assert.equal(host.main.detailViewerState.open, true, '不得越层关闭整个预览');

        // 3. 进入正文编辑且修改内容：按一次 Backquote 自动保存并回到预览
        clickEdit();
        await wait(20);
        const editor3 = host.document.querySelector('[data-detail-text-editor]');
        editor3.value = 'updated text';
        const exitDirtyEvent = backquote(editor3);
        host.document.dispatchEvent(exitDirtyEvent);
        await wait(30);
        assert.equal(exitDirtyEvent.prevented, true);
        assert.equal(savedBodies.length, 1, '有改动按 Backquote 应触发自动保存');
        assert.equal(savedBodies[0].text, 'updated text');
        assert.equal(host.main.detailViewerState.textEditing, false);
        assert.equal(host.main.detailViewerState.open, true, '保存后应留在预览而非关闭');
    } finally {
        restore();
        host.dispose();
        restoreTimers();
    }
});

test('AC5：真实加载 coverflow.js 全屏 Coverflow 时，连按 Backquote 不进编辑，F / Escape 关闭', async () => {
    const restoreTimers = trackUnrefTimers();
    const host = await createAssetViewHost({tab:'local'});
    const restore = installCanvasAndImageStubs(host.document);
    try {
        const coverflowUrl = pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/coverflow.js')).href;
        await import(coverflowUrl);

        let editCount = 0;
        let closeCount = 0;
        const prevFloating = globalThis.window.FloatingDismissal;
        globalThis.window.FloatingDismissal = null;
        globalThis.window.addEventListener('gw:asset-coverflow-edit', () => { editCount += 1; });
        globalThis.window.addEventListener('gw:asset-coverflow-closed', () => { closeCount += 1; });

        try {
            globalThis.window.dispatchEvent(new globalThis.window.CustomEvent('gw:asset-coverflow-open', {
                detail:{
                    mode:'fullscreen',
                    source:'registry',
                    scope:'library',
                    assets:[{id:'cf-1', name:'shot-1.png', kind:'image', url:'/assets/shot-1.png'}],
                    currentAssetId:'cf-1',
                },
            }));
            await wait(20);
            const surface = host.document.querySelector('[data-gw-coverflow="fullscreen"]');
            assert.ok(surface, '应打开全屏 Coverflow');

            const tap1 = backquote(surface);
            surface.dispatchEvent(tap1);
            const tap2 = backquote(surface);
            surface.dispatchEvent(tap2);
            await wait(20);
            assert.equal(tap1.prevented, true);
            assert.equal(tap2.prevented, true);
            assert.equal(editCount, 0, '连按 Backquote 不得进入编辑器');
            assert.ok(host.document.querySelector('[data-gw-coverflow="fullscreen"]'), 'Backquote 不得关闭 Coverflow');

            const fKey = keyEvent('f', 'KeyF', surface);
            surface.dispatchEvent(fKey);
            await wait(20);
            assert.equal(closeCount, 1);
            assert.equal(host.document.querySelector('[data-gw-coverflow="fullscreen"]'), null, 'F 应关闭全屏 Coverflow');

            // 再次打开并用 Escape 关闭
            globalThis.window.dispatchEvent(new globalThis.window.CustomEvent('gw:asset-coverflow-open', {
                detail:{
                    mode:'fullscreen',
                    source:'registry',
                    scope:'library',
                    assets:[{id:'cf-1', name:'shot-1.png', kind:'image', url:'/assets/shot-1.png'}],
                    currentAssetId:'cf-1',
                },
            }));
            await wait(20);
            const surface2 = host.document.querySelector('[data-gw-coverflow="fullscreen"]');
            assert.ok(surface2);
            surface2.dispatchEvent(keyEvent('Escape', 'Escape', surface2));
            await wait(20);
            assert.equal(closeCount, 2);
            assert.equal(host.document.querySelector('[data-gw-coverflow="fullscreen"]'), null, 'Escape 应关闭全屏 Coverflow');
        } finally {
            globalThis.window.FloatingDismissal = prevFloating;
        }
    } finally {
        restore();
        host.dispose();
        restoreTimers();
    }
});
