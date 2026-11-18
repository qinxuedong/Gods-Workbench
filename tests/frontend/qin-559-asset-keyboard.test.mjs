/** QIN-559：全屏浏览、视频帧步进与 Backquote 编辑手势。 */
import assert from 'node:assert/strict';
import path from 'node:path';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import { createAssetViewHost, wait } from './qin-602-asset-view-harness.mjs';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import test from 'node:test';

const MANAGER = readAssetManagerAggregate();
const COVERFLOW = readFileSync(new URL('../../web/js/modules/asset-manager/coverflow.js', import.meta.url), 'utf8');
const GUIDE = readFileSync(new URL('../../docs/guides/KEYBOARD-SHORTCUTS.md', import.meta.url), 'utf8');

function functionSource(source, name){
    let start = source.indexOf(`function ${name}(`);
    if(start < 0) start = source.indexOf(`async function ${name}(`);
    assert.ok(start >= 0, `缺少函数：${name}`);
    const open = source.indexOf('{', source.indexOf(')', start));
    let depth = 0;
    for(let index = open; index < source.length; index += 1){
        if(source[index] === '{') depth += 1;
        else if(source[index] === '}'){
            depth -= 1;
            if(depth === 0) return source.slice(start, index + 1);
        }
    }
    assert.fail(`函数未闭合：${name}`);
}

function keyEvent(key, code='Backquote', target=null){
    return {
        type:'keydown', key, code, target,
        repeat:false, ctrlKey:false, metaKey:false, altKey:false, isComposing:false,
        defaultPrevented:false, prevented:false, stopped:false,
        preventDefault(){ this.defaultPrevented = true; this.prevented = true; },
        stopPropagation(){ this.stopped = true; },
    };
}

test('资产库 Backquote 不再使用双击识别窗口', () => {
    assert.doesNotMatch(MANAGER, /detailBackquoteTappedTwice|DETAIL_BACKQUOTE_DOUBLE_TAP_MS/);
    assert.match(MANAGER, /if\(detailToggleShortcut && !keyboardEditing\)[\s\S]*?if\(event\.repeat\) return;[\s\S]*?if\(detailViewerState\.open\)\{\s*closeDetailPreview\(\)/);
});

test('全屏 Coverflow 的 Backquote 不进入编辑，F 与 Escape 退出', async () => {
    const prevSetInterval = globalThis.setInterval;
    const prevSetTimeout = globalThis.setTimeout;
    const intervals = new Set();
    const timeouts = new Set();
    globalThis.setInterval = (...args) => { const id = prevSetInterval(...args); id?.unref?.(); intervals.add(id); return id; };
    globalThis.setTimeout = (...args) => { const id = prevSetTimeout(...args); id?.unref?.(); timeouts.add(id); return id; };
    const host = await createAssetViewHost({tab:'local'});
    const StubProto = Object.getPrototypeOf(host.document.body);
    const origAppend = StubProto.append;
    if(typeof StubProto.append !== 'function'){
        StubProto.append = function(...nodes){
            nodes.forEach(n => this.appendChild(typeof n === 'string' ? host.document.createTextNode(n) : n));
        };
    }
    const prevFloating = globalThis.window.FloatingDismissal;
    globalThis.window.FloatingDismissal = null;
    try {
        await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/coverflow.js')).href);
        let editCount = 0;
        let closeCount = 0;
        globalThis.window.addEventListener('gw:asset-coverflow-edit', () => { editCount += 1; });
        globalThis.window.addEventListener('gw:asset-coverflow-closed', () => { closeCount += 1; });

        globalThis.window.dispatchEvent(new globalThis.window.CustomEvent('gw:asset-coverflow-open', {
            detail:{
                mode:'fullscreen', source:'registry', scope:'library',
                assets:[{id:'asset-1', name:'shot-1.png', kind:'image', url:'/assets/shot-1.png'}],
                currentAssetId:'asset-1',
            },
        }));
        await wait(20);
        const surface = host.document.querySelector('[data-gw-coverflow="fullscreen"]');
        assert.ok(surface);
        const tap = keyEvent('`', 'Backquote', surface);
        surface.dispatchEvent(tap);
        assert.equal(editCount, 0);
        assert.equal(tap.prevented, true);
        surface.dispatchEvent(keyEvent('`', 'Backquote', surface));
        assert.equal(editCount, 0);

        surface.dispatchEvent(keyEvent('f', 'KeyF', surface));
        await wait(20);
        assert.equal(closeCount, 1);

        globalThis.window.dispatchEvent(new globalThis.window.CustomEvent('gw:asset-coverflow-open', {
            detail:{
                mode:'fullscreen', source:'registry', scope:'library',
                assets:[{id:'asset-1', name:'shot-1.png', kind:'image', url:'/assets/shot-1.png'}],
                currentAssetId:'asset-1',
            },
        }));
        await wait(20);
        const surface2 = host.document.querySelector('[data-gw-coverflow="fullscreen"]');
        surface2.dispatchEvent(keyEvent('Escape', 'Escape', surface2));
        await wait(20);
        assert.equal(closeCount, 2);
        assert.match(COVERFLOW, /assets:\s*view\.items\.map\(value => value\.raw\)/);
    } finally {
        globalThis.window.FloatingDismissal = prevFloating;
        if(origAppend) StubProto.append = origAppend; else delete StubProto.append;
        for(const id of intervals) clearInterval(id);
        for(const id of timeouts) clearTimeout(id);
        globalThis.setInterval = prevSetInterval;
        globalThis.setTimeout = prevSetTimeout;
        host.dispose();
    }
});

test('画布 F 使用选择集，视频 A/D 逐帧且 F 切换全屏', () => {
    const selection = functionSource(MANAGER, 'detailMediaCanvasSelectedCoverflowAssets');
    const openSelection = functionSource(MANAGER, 'openDetailMediaCanvasSelectionCoverflow');
    const toggleFullscreen = functionSource(MANAGER, 'toggleDetailVideoFullscreen');
    assert.match(selection, /detailMediaCanvasExpandedObjectIds\(detailMediaCanvasSelectionOrder\(\)\)/);
    assert.match(openSelection, /mode:'fullscreen'/);
    assert.match(openSelection, /assets,/);
    assert.match(openSelection, /currentAssetId,/);
    assert.doesNotMatch(MANAGER, /hasCanvasFullscreenSelection/);
    assert.match(MANAGER, /gwCall\('preview', 'stepDetailVideoFrame', key === 'd' \? 1 : -1\)/);
    assert.match(MANAGER, /detailMediaCanvasSelectedCoverflowAssets\(\)\.length <= 1\)\)\{\s*event\.preventDefault\(\);\s*gwCall\('preview', 'toggleDetailVideoFullscreen'\)/);
    assert.match(MANAGER, /key === 'f'\)\{\s*event\.preventDefault\(\);\s*gwCall\('preview', 'toggleDetailVideoFullscreen'\)/);
    assert.match(toggleFullscreen, /document\.fullscreenElement === video/);
    assert.match(MANAGER, /title="上一帧（A）" aria-label="上一帧（A）"/);
    assert.match(MANAGER, /title="下一帧（D）" aria-label="下一帧（D）"/);
    assert.match(MANAGER, /<kbd>A<\/kbd><kbd>D<\/kbd> 上一帧 \/ 下一帧/);
    assert.match(MANAGER, /<kbd>F<\/kbd> 切换全屏/);
    assert.match(MANAGER, /data-detail-video-action="rate-reset" title="恢复原始速度（Z）"/);
    assert.match(MANAGER, /title="减速 0\.1 倍（X）"/);
    assert.match(MANAGER, /title="加速 0\.1 倍（C）"/);
});

test('图像编辑器单次 Backquote 返回预览，指南同步', () => {
    const closeEditor = functionSource(MANAGER, 'closeDetailImageEditor');
    const restoreView = functionSource(MANAGER, 'restoreDetailImageEditorCoverflow');
    const loadText = functionSource(MANAGER, 'loadDetailViewerText');
    assert.match(closeEditor, /restoreDetailImageEditorCoverflow\(state\)/);
    assert.match(restoreView, /gw:asset-coverflow-open/);
    assert.match(loadText, /detailViewerFileParams\(detailViewerState\.source, item\)/);
    assert.match(MANAGER, /gwCall\('preview', 'requestCloseDetailImageEditor', '返回素材预览'\)/);
    assert.match(MANAGER, /gwCall\('preview', 'exitDetailTextEditFromBackquote'/);
    assert.doesNotMatch(MANAGER, /detailTextEditorBackquoteInput/);
    assert.doesNotMatch(GUIDE, /500ms 内双击/);
    assert.match(MANAGER, /const registryFullscreenShortcut = event\.key === 'Enter'[\s\S]*?!event\.ctrlKey && !event\.metaKey && !event\.altKey/);
    // QIN-570：F 改为真全屏查看，Enter 保持 Coverflow，指南拆行
    assert.match(GUIDE, /`F` \| 真全屏查看当前选中图片\/视频/);
    assert.match(GUIDE, /`Enter` \| 打开当前选中图片\/视频的 Coverflow 轮播/);
    assert.match(GUIDE, /即时打开或关闭当前素材预览/);
    assert.match(GUIDE, /`A` \/ `D`/);
});
