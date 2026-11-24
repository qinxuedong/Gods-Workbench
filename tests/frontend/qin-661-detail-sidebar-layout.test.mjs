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

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createAssetViewHost } from './qin-602-asset-view-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '../..');
const CSS_PATH = path.join(REPO_ROOT, 'web/css/pages/asset-manager.css');

test('QIN-661 AC1：画布查看器外层与工具栏约束横向不溢出，避免右侧面板被挤出视口', () => {
    const css = readFileSync(CSS_PATH, 'utf8');
    assert.match(css, /\.asset-lightbox \{[^}]*width:100%;\s*max-width:100%;/);
    assert.match(css, /\.asset-detail-viewer-page \{[^}]*max-width:100%;[^}]*min-width:0;[^}]*overflow:hidden;/);
    assert.match(css, /\.asset-detail-viewer-body \{[^}]*width:100%;\s*max-width:100%;\s*min-width:0;[^}]*overflow:hidden;/);
    assert.match(css, /\.asset-detail-media-canvas-stage \{[^}]*max-width:100%;\s*min-width:0;\s*overflow:hidden;/);
    assert.match(css, /\.asset-detail-media-toolbar-primary \{[^}]*flex-wrap:wrap;/);
});

test('QIN-661 AC2：LUT 页与主视图右侧详情栏折叠态保持在 Grid 内容区列内，起止高度与黑金样式一致', () => {
    const css = readFileSync(CSS_PATH, 'utf8');
    assert.doesNotMatch(css, /\.registry-detail-panel\.is-collapsed\s*\{[^}]*position:\s*absolute/);
    assert.match(css, /\.registry-detail-panel\.is-collapsed\s*\{[^}]*position:relative;[^}]*height:100%;/);
    assert.match(css, /\.asset-manager-root\.asset-tree-layout\.asset-manager-root-page-lut\.registry-detail-collapsed\s*\{\s*grid-template-columns:240px minmax\(0,1fr\) 44px;\s*\}/);
    assert.match(css, /\.registry-detail-panel\.is-collapsed \.registry-detail-expand\s*\{/);
});

test('QIN-661 AC3：多素材画布右侧面板折叠态保留触发属性并对齐 LUT 黑金窄条展开结构', async () => {
    const css = readFileSync(CSS_PATH, 'utf8');
    assert.match(css, /\.asset-detail-viewer-body\.asset-detail-media-library-layout > \.asset-detail-media-sidebar\.is-collapsed\s*\{/);
    assert.match(css, /\.asset-detail-media-sidebar-expand-label\s*\{[^}]*writing-mode:vertical-rl;/);

    const host = await createAssetViewHost({ tab: null });
    try {
        const media = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/media-canvas.js')).href);
        const main = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager.js')).href);
        const item = { id: 'ast_1', name: 'Diff_00001.png', asset_type: 'image', media_type: 'image' };
        Object.assign(main.detailViewerState, {
            open: true,
            source: 'registry',
            originTab: 'registry',
            index: 0,
            items: [item],
            mediaCanvasItems: [item],
            mediaCanvasActiveId: item.id,
            mediaCanvasLibraryItems: [item],
            mediaCanvasLibraryVisible: true,
            mediaCanvasLibraryCollapsed: true,
            mediaCanvasSidebarView: 'detail',
        });
        main.setDetailMediaCanvasLibraryCollapsed(true);
        const collapsedHtml = media.renderDetailMediaCanvasLibrary();
        assert.match(collapsedHtml, /asset-detail-media-sidebar is-collapsed/);
        assert.match(collapsedHtml, /data-detail-media-library-toggle/);
        assert.match(collapsedHtml, /asset-detail-media-sidebar-expand/);
        assert.match(collapsedHtml, /资产详情/);
    } finally {
        host.dispose();
    }
});
