/** QIN-570：资产库 F 键真全屏查看当前素材（不再打开 Coverflow）。 */
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const MANAGER = readAssetManagerAggregate();
const GUIDE = readFileSync(new URL('../../docs/guides/KEYBOARD-SHORTCUTS.md', import.meta.url), 'utf8');
const CSS = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

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

function createShowHarness(assetsPreview){
    return new Function('document', 'assetPreviewUrl', `
        ${functionSource(MANAGER, 'registryFullscreenViewerMediaUrl')}
        ${functionSource(MANAGER, 'registryFullscreenViewerPosterUrl')}
        ${functionSource(MANAGER, 'registryFullscreenViewerShow')}
        return {show:registryFullscreenViewerShow};
    `)(createFakeDocument(), assetsPreview);
}

function createFakeElement(tag){
    return {
        tagName: tag,
        src: '',
        alt: '',
        poster: '',
        controls: false,
        autoplay: false,
        playsInline: false,
        decoding: '',
        setAttribute(){},
        append(){},
    };
}

function createFakeDocument(){
    const created = [];
    return {
        created,
        createElement(tag){
            const element = createFakeElement(tag);
            created.push(element);
            return element;
        },
    };
}

function createShowState(items){
    const spans = {name:{textContent:''}, count:{textContent:''}};
    const stages = [];
    return {
        items,
        index: 0,
        stages,
        overlay: {
            querySelector(selector){
                if(selector.includes('-stage')){
                    const stage = {children:[], replaceChildren(){ this.children.length = 0; }, append(child){ this.children.push(child); }};
                    stages.push(stage);
                    return stage;
                }
                if(selector.includes('-name')) return spans.name;
                if(selector.includes('-count')) return spans.count;
                return null;
            },
        },
        spans,
    };
}

test('F 路由真全屏查看器，Enter 保持 Coverflow，查看器内 F/Esc 关闭', () => {
    assert.match(MANAGER, /function openRegistryAssetFullscreenViewer\(trigger=null, options=\{\}\)\{/);
    // QIN-591：Enter/F 派发 gwCall 化（登记在案），快捷键语义不变。
    assert.match(MANAGER, /if\(event\.key === 'Enter'\)\{\s*gwCall\('coverflow-bridge', 'openRegistryCoverflow', document\.activeElement/);
    assert.match(MANAGER, /gwCall\('preview', 'openRegistryAssetFullscreenViewer', document\.activeElement, \{currentAssetId:String\(current\.asset_id \|\| current\.id \|\| ''\)\}\)/);
    assert.match(MANAGER, /toLowerCase\(\) === 'f' && registryFullscreenViewerState\)\{/);
    assert.match(MANAGER, /document\.addEventListener\('keydown', state\.onKeydown, true\)/);
    assert.match(MANAGER, /if\(registryFullscreenViewerState === state && !document\.fullscreenElement\)\{\s*closeRegistryAssetFullscreenViewer\(\);/);
    assert.match(MANAGER, /markAssetFloatingSurface\(overlay, \{surface:'asset-fsviewer'/);
    assert.match(MANAGER, /overlay\.tabIndex = -1;\s*overlay\.focus\(\{preventScroll:true\}\)/);
    assert.match(MANAGER, /enterPromise\.catch\(err => console\.warn\('\[AssetManager\] 进入真全屏失败（回落页面内覆盖层）:', err\)\)/);
    assert.match(GUIDE, /`F` \| 真全屏查看当前选中图片\/视频/);
    assert.match(GUIDE, /`Enter` \| 打开当前选中图片\/视频的 Coverflow 轮播/);
    assert.match(CSS, /\.gw-asset-fsviewer \{ position:fixed; inset:0;/);
});

test('查看器渲染原片地址并循环切换，底部状态行计数', () => {
    const harness = createShowHarness(url => `${url}!preview`);
    const items = [
        {id:'a', mediaKind:'image', name:'owl.png', mediaSrc:'/media/a.png'},
        {id:'b', mediaKind:'video', name:'clip.mp4', mediaSrc:'/media/b.mp4'},
        {id:'c', mediaKind:'image', name:'third.png', mediaSrc:'/media/c.png'},
    ];
    const state = createShowState(items);
    harness.show(state, 0);
    assert.equal(state.index, 0);
    assert.equal(state.spans.name.textContent, 'owl.png');
    assert.equal(state.spans.count.textContent, '1 / 3');
    harness.show(state, -1);
    assert.equal(state.index, 2);
    assert.equal(state.spans.count.textContent, '3 / 3');
    harness.show(state, 1);
    assert.equal(state.index, 1);
    assert.equal(state.spans.name.textContent, 'clip.mp4');
    assert.equal(state.spans.count.textContent, '2 / 3');
    harness.show(state, 3);
    assert.equal(state.index, 0);
    const stages = state.stages;
    assert.equal(stages[0].children[0].src, '/media/a.png');
    assert.equal(stages[0].children[0].alt, 'owl.png');
    assert.equal(stages[2].children[0].src, '/media/b.mp4');
    assert.equal(stages[2].children[0].controls, true);
    assert.equal(stages[2].children[0].autoplay, true);
    assert.equal(stages[2].children[0].playsInline, true);
    assert.equal(stages[2].children[0].poster, '/media/b.mp4!preview');
});

test('关闭真全屏查看器：移除监听与 overlay、退出全屏并返还焦点', () => {
    const closeHarness = new Function('document', `
        let registryFullscreenViewerState = null;
        // QIN-591：拆分后写入经主文件导出的 setter，桩把 setter 落回本夹具的局部状态。
        function setRegistryFullscreenViewerState(value){ registryFullscreenViewerState = value; }
        ${functionSource(MANAGER, 'closeRegistryAssetFullscreenViewer')}
        return {
            close:closeRegistryAssetFullscreenViewer,
            setState:value => { registryFullscreenViewerState = value; },
        };
    `)(createFakeCloseDocument());
    assert.equal(closeHarness.close(), false);

    const events = createFakeCloseDocument();
    const closeHarness2 = new Function('document', `
        let registryFullscreenViewerState = null;
        // QIN-591：拆分后写入经主文件导出的 setter，桩把 setter 落回本夹具的局部状态。
        function setRegistryFullscreenViewerState(value){ registryFullscreenViewerState = value; }
        ${functionSource(MANAGER, 'closeRegistryAssetFullscreenViewer')}
        return {
            close:closeRegistryAssetFullscreenViewer,
            setState:value => { registryFullscreenViewerState = value; },
        };
    `)(events);
    const state = {
        overlay:{remove(){ events.removedOverlay += 1; }},
        trigger:{focus(){ events.focusCalls += 1; }},
        onKeydown(){},
        onFullscreenChange(){},
    };
    closeHarness2.setState(state);
    assert.equal(closeHarness2.close(), true);
    assert.equal(events.removedOverlay, 1);
    assert.equal(events.focusCalls, 1);
    assert.equal(events.captureRemovals, 1);
    assert.equal(closeHarness2.close(), false);
});

function createFakeCloseDocument(){
    return {
        fullscreenElement: null,
        removedOverlay: 0,
        focusCalls: 0,
        captureRemovals: 0,
        removeEventListener(name, fn, options){
            if(name === 'keydown' && options === true) this.captureRemovals += 1;
        },
        exitFullscreen(){
            return {catch(){}};
        },
    };
}

