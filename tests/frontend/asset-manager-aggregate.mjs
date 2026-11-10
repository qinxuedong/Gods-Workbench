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

// QIN-591：asset-manager 聚合源读取 helper（源文本断言测试的稳定读取方式）。
//
// 用途：web/js/modules/asset-manager.js 已物理拆分为主文件 + asset-manager/ 延后域模块。
// 既有源文本断言测试验证的是「资产库模块的源码文本」，拆分后该文本分布在多个文件中。
// 聚合源 = 主文件 + state.js + 15 个域文件，按「各文件承载内容在原 asset-manager.js
// 中的最小行号」保序拼接（非目录字典序——字典序不等于原行序），使按行序书写的
// includes / 正则断言与切片（indexOf + slice）语义与拆分前保持一致。
//
// 保序约定（新增域文件时必须按行序插入 ASSET_MANAGER_FILES，禁止改用目录枚举）：
//   1. 主文件排第一；
//   2. state.js（GW_DEFERRED 注册表，主文件顶部即 import）排第二；
//   3. 其余域文件按承载内容在原文件中的最小行号升序硬编码：
//      settings(原98) → organize(281) → preview(420) → legacy-pages(465) →
//      recycle(466) → upload(707) → inbox(765) → media-canvas(805) → batch(936) →
//      canvas-bridge(5359) → coverflow-bridge(6130) → lut(7077) → tags(8449) →
//      governance(9868)。
//
// 口径说明：asset-manager/ 目录下更早票据拆出的既有子包（api.js/http.js/classification.js/
// storage.js/formatters.js/path-utils.js/workflow-assets.js/provenance.js/
// collect-provenance.js/lut-preview.js/coverflow.js）的内容从未属于原 asset-manager.js
// 的源文本，不参与本聚合。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const MODULE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../web/js/modules');

/**
 * 聚合源文件序列（相对 web/js/modules/ 的仓库路径，正斜杠）。
 * 主文件第一，其余按承载内容在原 asset-manager.js 中的最小行号升序保序拼接。
 */
export const ASSET_MANAGER_FILES = [
    'asset-manager.js',
    'asset-manager/state.js',
    'asset-manager/settings.js',
    'asset-manager/organize.js',
    'asset-manager/preview.js',
    'asset-manager/legacy-pages.js',
    'asset-manager/recycle.js',
    'asset-manager/upload.js',
    'asset-manager/inbox.js',
    'asset-manager/media-canvas.js',
    'asset-manager/batch.js',
    'asset-manager/canvas-bridge.js',
    'asset-manager/coverflow-bridge.js',
    'asset-manager/lut.js',
    'asset-manager/tags.js',
    'asset-manager/governance.js',
];

let cached = null;

/**
 * 返回 asset-manager 模块聚合源字符串（主文件 + state + 15 个域文件保序拼接）。
 * 任一序列文件缺失时抛出含文件名与期望序列的清晰错误，不静默回退。
 */
export function readAssetManagerAggregate() {
    if (cached !== null) return cached;
    const parts = [];
    for (const relative of ASSET_MANAGER_FILES) {
        const absolute = path.join(MODULE_ROOT, relative);
        let text;
        try {
            text = readFileSync(absolute, 'utf8');
        } catch (error) {
            throw new Error(
                `asset-manager 聚合源文件缺失或不可读：${relative}（${absolute}）。` +
                `ASSET_MANAGER_FILES 序列与仓库实际文件不一致，请核对拆分产物或保序序列。原因：${error.message}`,
            );
        }
        parts.push(text);
    }
    cached = parts.join('\n');
    return cached;
}
