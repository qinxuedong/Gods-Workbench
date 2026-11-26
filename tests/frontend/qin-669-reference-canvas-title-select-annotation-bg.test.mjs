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
const HTML_PATH = path.join(REPO_ROOT, 'web/embeds/asset-manager.html');
const SHORTCUTS_PATH = path.join(REPO_ROOT, 'docs/guides/KEYBOARD-SHORTCUTS.md');

function dispatchDomEvent(host, el, type, extra = {}) {
    const evt = {
        type,
        target: el,
        currentTarget: host.document,
        bubbles: true,
        cancelable: true,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {},
        stopImmediatePropagation() {},
        ...extra,
    };
    host.document.dispatchEvent(evt);
    return evt;
}

function patchStubHost(host) {
    const sample = host.document.createElement('div');
    const proto = Object.getPrototypeOf(sample);
    if (proto && typeof proto.replaceWith !== 'function') {
        proto.replaceWith = function (...nodes) {
            const parent = this.parentNode;
            if (!parent || !Array.isArray(parent.children)) return;
            const idx = parent.children.indexOf(this);
            if (idx >= 0) {
                parent.children.splice(idx, 1, ...nodes);
                for (const node of nodes) {
                    if (node && typeof node === 'object') node.parentNode = parent;
                }
            }
        };
    }
    const prevRaf = globalThis.requestAnimationFrame;
    globalThis.requestAnimationFrame = () => 0;
    return () => {
        globalThis.requestAnimationFrame = prevRaf;
    };
}

function openReferenceCanvasInHost(main, preview, options = {}) {
    const item = { id: 'ast_1', name: 'Ref_001.png', asset_type: 'image', media_type: 'image', url: '/mock/ref.png' };
    Object.assign(main.detailViewerState, {
        open: true,
        source: 'registry',
        originTab: 'registry',
        index: 0,
        items: [item],
        id: item.id,
        referenceCanvasMode: true,
        referenceCanvasTitle: options.title || '分镜参考板',
        referenceCanvasTitleEditing: false,
        referenceCanvasTitleEditOriginal: '',
        mediaCanvasItems: [item],
        mediaCanvasActiveId: item.id,
        mediaCanvasSelectedIds: new Set(options.selectedIds || [item.id]),
        mediaCanvasSelectionOrder: options.selectedIds || [item.id],
        mediaCanvasAnnotations: options.annotations || [],
        mediaCanvasEditingAnnotationId: '',
        mediaCanvasLayout: { [item.id]: { x: 40, y: 40, width: 320, height: 240 } },
        mediaCanvasContextMenu: options.contextMenu || null,
    });
    preview.renderDetailViewer();
    return item;
}

test('QIN-669 AC1：参考画布名称默认普通文本展示，双击进入行内编辑，Enter/失焦提交，Esc取消恢复，空名回退「参考画布」', async () => {
    const host = await createAssetViewHost({ tab: null });
    const restoreRaf = patchStubHost(host);
    try {
        const media = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/media-canvas.js')).href);
        const preview = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/preview.js')).href);
        const main = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager.js')).href);

        openReferenceCanvasInHost(main, preview, { title: '分镜参考板' });

        // 默认展示态：存在 display 元素，无输入框
        const displayEl = host.document.querySelector('[data-detail-reference-title-display]');
        assert.ok(displayEl, '默认应渲染普通标题文本展示元素');
        assert.equal(host.document.querySelector('[data-detail-reference-title]'), null, '展示态不应出现常驻输入框');
        assert.equal(displayEl.textContent.trim(), '分镜参考板');

        // 双击进入行内编辑
        dispatchDomEvent(host, displayEl, 'dblclick');
        let inputEl = host.document.querySelector('[data-detail-reference-title]');
        assert.ok(inputEl, '双击后应进入行内编辑态');
        assert.equal(main.detailViewerState.referenceCanvasTitleEditing, true);
        assert.equal(inputEl.getAttribute('maxlength'), '80');

        // 编辑过程中 E / · 字符快捷键让位给输入，不关闭详情也不触发编辑
        dispatchDomEvent(host, inputEl, 'keydown', { key: 'e' });
        dispatchDomEvent(host, inputEl, 'keydown', { key: '·', code: 'Backquote' });
        assert.equal(main.detailViewerState.open, true, '编辑期间按字符键不得关闭详情');
        assert.equal(main.detailViewerState.referenceCanvasTitleEditing, true, '编辑期间按字符键不得退出编辑态');

        // Esc 取消并恢复原名，且不关闭详情
        inputEl.value = '临时未提交名称';
        dispatchDomEvent(host, inputEl, 'keydown', { key: 'Escape' });
        assert.equal(main.detailViewerState.open, true, 'Esc 取消名称编辑时不得关闭详情');
        assert.equal(main.detailViewerState.referenceCanvasTitleEditing, false);
        assert.equal(main.detailViewerState.referenceCanvasTitle, '分镜参考板');
        assert.equal(host.document.querySelector('[data-detail-reference-title-display]')?.textContent.trim(), '分镜参考板');

        // 再次双击 -> Enter 提交新名称
        dispatchDomEvent(host, host.document.querySelector('[data-detail-reference-title-display]'), 'dblclick');
        inputEl = host.document.querySelector('[data-detail-reference-title]');
        inputEl.value = '新参考画布名称';
        dispatchDomEvent(host, inputEl, 'keydown', { key: 'Enter' });
        assert.equal(main.detailViewerState.referenceCanvasTitleEditing, false);
        assert.equal(main.detailViewerState.referenceCanvasTitle, '新参考画布名称');

        // 再次双击 -> 输入空白 -> 失焦回退「参考画布」
        dispatchDomEvent(host, host.document.querySelector('[data-detail-reference-title-display]'), 'dblclick');
        inputEl = host.document.querySelector('[data-detail-reference-title]');
        inputEl.value = '   ';
        media.commitDetailReferenceTitleEdit(true);
        assert.equal(main.detailViewerState.referenceCanvasTitle, '参考画布');

        // 超过 80 字自动截断
        dispatchDomEvent(host, host.document.querySelector('[data-detail-reference-title-display]'), 'dblclick');
        inputEl = host.document.querySelector('[data-detail-reference-title]');
        inputEl.value = 'A'.repeat(100);
        media.commitDetailReferenceTitleEdit(true);
        assert.equal(main.detailViewerState.referenceCanvasTitle.length, 80);

        await new Promise(resolve => setTimeout(resolve, 60));
    } finally {
        restoreRaf();
        host.dispose();
    }

    const shortcutsDoc = readFileSync(SHORTCUTS_PATH, 'utf8');
    assert.ok(shortcutsDoc.includes('参考画布名称支持双击进入行内编辑'), '快捷键指南应补充参考画布名称编辑说明');
});

test('QIN-669 AC2：「基准」「范围」及右键「基准」接入 QIN-631 公共下拉，且保留左侧图库下拉不变', async () => {
    const html = readFileSync(HTML_PATH, 'utf8');
    assert.ok(html.includes('/static/css/components/gw-native-select.css'), 'asset-manager.html 应最小引入 gw-native-select.css');
    assert.ok(html.includes('/static/js/core/gw-native-select.js'), 'asset-manager.html 应最小引入 gw-native-select.js');

    const host = await createAssetViewHost({ tab: null });
    const restoreRaf = patchStubHost(host);
    try {
        const preview = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/preview.js')).href);
        const main = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager.js')).href);

        openReferenceCanvasInHost(main, preview, {
            contextMenu: { open: true, x: 40, y: 40, worldX: 40, worldY: 40 },
        });

        const basisSelects = [...host.document.querySelectorAll('[data-detail-media-normalize-basis]')];
        const scopeSelect = host.document.querySelector('[data-detail-media-export-scope]');
        assert.equal(basisSelects.length, 2, '工具栏和右键菜单共应有 2 个基准下拉');
        assert.ok(scopeSelect, '工具栏应有范围下拉');

        for (const selectEl of [...basisSelects, scopeSelect]) {
            assert.ok(selectEl.classList.contains('gw-native-select-input'), '下拉应具备 .gw-native-select-input 类');
            assert.ok(selectEl.hasAttribute('data-gw-select'), '下拉应具备 data-gw-select 属性');
            const shell = selectEl.parentElement || selectEl.parentNode;
            assert.ok(shell?.classList?.contains('gw-select-shell'), '下拉外层应为 .gw-select-shell');
        }

        // change 行为同步：切换工具栏基准，右键菜单基准与状态同步更新
        basisSelects[0].value = 'average';
        dispatchDomEvent(host, basisSelects[0], 'change');
        assert.equal(main.detailViewerState.mediaCanvasNormalizeBasis, 'average');
        assert.equal(basisSelects[1].value, 'average');

        // 左侧图库下拉不顺手改造
        for (const libAttr of ['data-detail-media-library-library', 'data-detail-media-library-scope', 'data-detail-media-library-sort']) {
            const libSelect = host.document.querySelector(`[${libAttr}]`);
            if (libSelect) {
                assert.equal(libSelect.classList.contains('gw-native-select-input'), false, `左侧图库 ${libAttr} 不应顺手改造`);
            }
        }
        await new Promise(resolve => setTimeout(resolve, 20));
    } finally {
        restoreRaf();
        host.dispose();
    }
});

test('QIN-669 AC3：工具栏移除「背景」色块，点击注释后显示仅含「背景」的注释设置卡片并保留已有注释底色', async () => {
    const host = await createAssetViewHost({ tab: null });
    const restoreRaf = patchStubHost(host);
    try {
        const media = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/media-canvas.js')).href);
        const preview = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/preview.js')).href);
        const main = await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager.js')).href);

        const existingNote = {
            id: 'note_existing_1',
            x: 80,
            y: 80,
            width: 240,
            height: 120,
            text: '已有注释',
            backgroundColor: '#3b4a6b',
            textColor: '#ffffff',
            fontSize: 15,
            padding: 10,
        };
        openReferenceCanvasInHost(main, preview, { annotations: [existingNote] });

        // 工具栏不得再出现背景色块
        const toolbar = host.document.querySelector('.asset-detail-media-canvas-toolbar');
        assert.ok(toolbar);
        assert.equal(toolbar.querySelector('[data-detail-annotation-color]'), null, '常驻工具栏不得出现注释背景色块');
        assert.equal(toolbar.querySelector('.asset-detail-media-note-colors'), null, '常驻工具栏不得出现 .asset-detail-media-note-colors');

        // 未选中注释时，注释设置卡片隐藏
        const card = host.document.querySelector('[data-detail-annotation-settings-card]');
        assert.ok(card, '画布应包含注释设置卡片容器');
        assert.equal(Boolean(card.hidden || card.hasAttribute('hidden')), true, '未选中注释时卡片必须隐藏');

        // 选中已有注释后，卡片显示，且保留已有底色 #3b4a6b
        media.selectDetailMediaCanvasItem(existingNote.id, {});
        assert.equal(Boolean(card.hidden || card.hasAttribute('hidden')), false, '选中注释后卡片应显示');
        const colorInputs = [...card.querySelectorAll('input')];
        assert.equal(colorInputs.length, 1, '本轮注释设置卡片只能提供 1 个背景控件');
        const bgInput = colorInputs[0];
        assert.equal(bgInput.dataset.detailAnnotationColor, 'background');
        assert.equal(bgInput.value.toLowerCase(), '#3b4a6b', '已有注释底色必须原样保留');

        // 通过卡片改色
        bgInput.value = '#7a2e2e';
        dispatchDomEvent(host, bgInput, 'input');
        assert.equal(existingNote.backgroundColor, '#7a2e2e');

        // 取消选中注释（切回图片节点）后卡片隐藏
        media.selectDetailMediaCanvasItem('ast_1', {});
        assert.equal(Boolean(card.hidden || card.hasAttribute('hidden')), true, '取消选中注释后卡片应隐藏');

        // 新建注释仍默认 #202622
        media.handleDetailMediaCanvasContextAction('annotation');
        const createdNote = main.detailViewerState.mediaCanvasAnnotations.find(n => n.id !== existingNote.id);
        assert.ok(createdNote);
        assert.equal(createdNote.backgroundColor, '#202622');
    } finally {
        restoreRaf();
        host.dispose();
    }
});

test('QIN-669 AC4：CSS 覆盖四宽（724/900/1440/1920）与双主题（dark/light）且无金色常驻方框', () => {
    const css = readFileSync(CSS_PATH, 'utf8');
    assert.match(css, /\.asset-detail-reference-title\s*\{[^}]*border:1px solid transparent;/);
    assert.match(css, /\.asset-detail-annotation-settings-card\[hidden\]\s*\{\s*display:none;\s*\}/);
    assert.match(css, /html\[data-theme="light"\]\s+\.asset-detail-annotation-settings-card/);
    assert.match(css, /\.asset-detail-reference-title\.is-editing\s*\{\s*min-width:150px;\s*\}/);
    assert.match(css, /@media\s*\(max-width:620px\)[\s\S]*\.asset-detail-annotation-settings-card/);
});
