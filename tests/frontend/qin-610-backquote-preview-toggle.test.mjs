import assert from 'node:assert/strict';
import test from 'node:test';
import {createAssetViewHost} from './qin-602-asset-view-harness.mjs';

function backquote(target){
    const event = {
        type:'keydown', key:'`', code:'Backquote', target,
        repeat:false, ctrlKey:false, metaKey:false, altKey:false, isComposing:false,
        prevented:false, stopped:false,
        preventDefault(){ this.prevented = true; },
        stopPropagation(){ this.stopped = true; },
    };
    return event;
}

test('选中资产后 Backquote 即时开关预览，打开键不计作关闭键', async () => {
    const host = await createAssetViewHost({tab:'local'});
    try {
        const asset = {
            id:'qin-610-text', name:'QIN-610 note.txt', filename:'qin-610-note.txt',
            kind:'text', mime_type:'text/plain', url:'/assets/qin-610-note.txt', content:'fixture',
        };
        host.main.setLocalItemMap(new Map([[asset.id, asset]]));
        host.main.setAssetLibrary({libraries:[{id:'test',categories:[{items:[asset]}]}]});
        const card = host.document.createElement('div');
        card.setAttribute('data-detail-source', 'local');
        card.setAttribute('data-detail-id', asset.id);
        card.setAttribute('data-local-card', asset.id);
        host.root.appendChild(card);
        host.document.activeElement = card;

        const openEvent = backquote(card);
        host.document.dispatchEvent(openEvent);
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.equal(openEvent.prevented, true);
        assert.ok(host.document.querySelector('.asset-detail-viewer'), '首次 Backquote 应显示素材预览');
        assert.equal(host.main.detailViewerState.open, true);
        assert.equal(host.main.detailViewerState.id, asset.id);

        const closeEvent = backquote(host.document.body);
        host.document.dispatchEvent(closeEvent);
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.equal(closeEvent.prevented, true);
        assert.equal(host.document.querySelector('.asset-detail-viewer'), null, '第二次 Backquote 应立即关闭预览');
        assert.equal(host.main.detailViewerState.open, false);
    } finally {
        host.dispose();
    }
});
