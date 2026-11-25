// Copyright 2026 Gods-Workbench Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * QIN-653（GitHub #814）全类型资产详情面板契约。
 *
 * AC1 面板与画布可视区同顶；AC2 删除顶部标题行并把两个分页按钮置于顶部、黑金激活态；
 * AC3 去类型文字；AC4 存储位置取真实素材库根路径；AC5 去底部按钮与快捷键提示。
 * 本文件只加载真实产品模块（整棵 web/js 复制到临时目录后原生 import），不用源码切片替身。
 */

import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createAssetViewHost} from './qin-602-asset-view-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const CSS_PATH = path.join(ROOT, 'web/css/pages/asset-manager.css');
const PREVIEW_PATH = path.join(ROOT, 'web/js/modules/asset-manager/preview.js');

function makeItem(overrides = {}) {
    return {
        id: 'ast_qin653', name: 'sample.png', kind: 'indexed', size: 2048,
        storage_root_id: 'root_qin653', storage_root_name: 'output',
        canonical_relpath: 'nested/sample.png',
        ...overrides,
    };
}

async function loadDetailModules(host) {
    const base = path.join(host.tree, 'web/js/modules/asset-manager');
    const url = name => pathToFileURL(path.join(base, name)).href;
    const preview = await import(url('preview.js'));
    const media = await import(url('media-canvas.js'));
    return {preview, media};
}

const ROOTS = [
    {id: 'other', name: 'another', path: '/mnt/other'},
    {id: 'root_qin653', name: 'output', path: 'D:\\素材根目录\\output'},
];

test('QIN-653 AC4：存储位置由已登记素材库根路径与规范相对路径拼接，不再请求缺 path 的接口', async () => {
    const host = await createAssetViewHost({tab: null});
    try {
        const {preview} = await loadDetailModules(host);
        const item = makeItem();
        const location = preview.detailItemStorageLocation('registry', item, ROOTS);
        assert.equal(location.absolute, 'D:\\素材根目录\\output\\nested\\sample.png');
        assert.equal(location.rootId, 'root_qin653');

        const rows = preview.detailViewerMetaRows('registry', item, 'image', ROOTS);
        const storage = rows.find(([label, , kind]) => label === '存储位置' && kind === 'path');
        assert.ok(storage, '详情行缺少「存储位置」');
        assert.equal(storage[1], location.absolute);
        assert.doesNotMatch(storage[1], /正在读取|缺少必填参数/, '不允许把加载占位或后端错误当路径展示');

        const params = preview.detailFileInfoParams('registry', item, ROOTS);
        assert.equal(params.get('path'), location.absolute);
        assert.equal(params.get('root_id'), 'root_qin653');
        assert.equal(params.get('asset_id'), null, 'asset_id 不是 file-info 的合法参数');
        assert.equal(params.get('url'), null, 'url 不是 file-info 的合法参数');

        // 未登记根时只展示可核实的逻辑路径，绝不拼出一个假绝对路径。
        const unknown = makeItem({storage_root_id: 'missing', storage_root_name: 'missing'});
        const fallback = preview.detailItemStorageLocation('registry', unknown, ROOTS);
        assert.equal(fallback.absolute, '');
        assert.equal(fallback.value, 'missing/nested/sample.png');
        assert.equal(preview.detailFileInfoParams('registry', unknown, ROOTS).toString(), '');
    } finally {
        host.dispose();
    }
});

test('QIN-653 AC2/3/5：详情内容全类型统一，去类型文字、底部按钮与快捷键提示', async () => {
    const host = await createAssetViewHost({tab: null});
    try {
        const {preview} = await loadDetailModules(host);
        for (const [type, item] of [
            ['image', makeItem()],
            ['video', makeItem({id: 'ast_video', name: 'clip.mp4', canonical_relpath: 'nested/clip.mp4'})],
            ['audio', makeItem({id: 'ast_audio', name: 'voice.mp3', canonical_relpath: 'nested/voice.mp3'})],
            ['text', makeItem({id: 'ast_text', name: 'note.txt', canonical_relpath: 'nested/note.txt'})],
        ]) {
            const rows = preview.detailViewerMetaRows('registry', item, type);
            const markup = preview.renderDetailViewerInspectorContent(item, type, [], rows, '/media');
            assert.doesNotMatch(markup, /asset-detail-actions|data-detail-export-pdf|data-detail-viewer-close/, `${type} 仍保留底部按钮`);
            assert.doesNotMatch(markup, /asset-detail-shortcuts|<kbd>/, `${type} 仍保留快捷键提示`);
            assert.doesNotMatch(markup, /<div class="asset-detail-title"><span>/, `${type} 仍保留类型徽标`);
            if (markup.includes('data-detail-name-input')) {
                assert.match(markup, /data-detail-name-input/, '可重命名状态保留输入框');
            } else {
                assert.match(markup, /<dt>文件名<\/dt>/, `${type} 应把文件名放到事实列表第一行`);
                if (markup.includes('<dt>文件格式</dt>')) {
                    assert.ok(markup.indexOf('<dt>文件名</dt>') < markup.indexOf('<dt>文件格式</dt>'), `${type} 文件名应在文件格式之前`);
                }
            }
        }
        const model = preview.renderDetailModelInspector(makeItem({id: 'ast_model', name: 'a.glb'}), [], [], []);
        assert.doesNotMatch(model, />3D 文件</);
        assert.doesNotMatch(model, /asset-detail-actions|asset-detail-shortcuts/);
    } finally {
        host.dispose();
    }
});

test('QIN-653 AC2：视频详情保留存储位置，侧栏顶部只有两个分页按钮与折叠控件', async () => {
    const host = await createAssetViewHost({tab: null});
    try {
        const {preview, media} = await loadDetailModules(host);
        const main = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager.js')).href);
        const item = makeItem({id: 'ast_video', name: 'clip.mp4', canonical_relpath: 'clip.mp4', metadata: {width: 1280, height: 720}});
        const rows = preview.detailViewerMetaRows('registry', item, 'video');
        const remaining = preview.detailViewerMetaRows;
        assert.equal(typeof remaining, 'function');
        // 视频详情走 detailVideoRemainingRows：历史筛的是「文件位置」，与行标签「存储位置」不一致。
        const videoHtml = preview.renderDetailViewerInspectorContent(item, 'video', [], rows, '/media');
        assert.match(videoHtml, /存储位置/, '视频详情看不到存储位置');
        assert.match(videoHtml, /output\/clip\.mp4/);

        Object.assign(main.detailViewerState, {
            open: true, source: 'registry', originTab: 'registry', index: 0,
            items: [item], mediaCanvasItems: [item], mediaCanvasActiveId: item.id,
            mediaCanvasLibraryItems: [item], mediaCanvasLibraryVisible: false,
            mediaCanvasLibraryCollapsed: false, mediaCanvasSidebarView: 'detail',
        });
        const sidebar = media.renderDetailMediaCanvasLibrary();
        assert.match(sidebar, /asset-detail-media-sidebar-tabs/);
        assert.match(sidebar, /data-detail-media-sidebar-view="detail"/);
        assert.match(sidebar, /data-detail-media-sidebar-view="library"/);
        assert.doesNotMatch(sidebar, /<header>/, '顶部冗余标题行未删除');
        assert.match(sidebar, /data-detail-media-library-toggle/, '折叠入口必须保留');
        assert.ok(sidebar.indexOf('data-detail-media-sidebar-view="detail"') < sidebar.indexOf('data-detail-media-sidebar-detail'), '分页按钮必须在详情内容之前');
    } finally {
        host.dispose();
    }
});

test('QIN-653 AC1/AC2：样式契约把面板顶边绑定到画布工具栏高度，激活态使用黑金而非白底', () => {
    const css = readFileSync(CSS_PATH, 'utf8');
    assert.match(css, /\.asset-detail-viewer-body\.asset-detail-media-library-layout > \.asset-detail-media-sidebar \{ margin-top:var\(--detail-canvas-toolbar-height,82px\); height:calc\(100% - var\(--detail-canvas-toolbar-height,82px\)\); \}/);
    const previewSource = readFileSync(PREVIEW_PATH, 'utf8');
    assert.match(previewSource, /overlay\.style\.setProperty\('--detail-canvas-toolbar-height'/);
    assert.match(css, /\.asset-detail-media-sidebar-tabs button\.is-active \{ border-color:var\(--gold-glow/);
    assert.doesNotMatch(css, /\.asset-detail-media-sidebar-tabs button\.is-active \{ border-color:var\(--text\); background:var\(--text\)/);
    assert.match(css, /\.asset-detail-media-sidebar\.is-detail-view \{ grid-template-rows:38px minmax\(0,1fr\); \}/);
});
