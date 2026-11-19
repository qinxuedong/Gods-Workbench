import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const manager = readAssetManagerAggregate();
const api = readFileSync(new URL('../../web/js/modules/asset-manager/api.js', import.meta.url), 'utf8');

test('QIN-553 / QIN-621：内置 LUT 登记走服务端幂等接口，已取消隐藏态集合', () => {
    assert.match(api, /ensureBuiltinLuts\(reveal = false, init = \{\}\)/);
    assert.match(api, /\/api\/asset-registry\/builtin-luts\/ensure\$\{suffix\}/);
    assert.match(api, /method: 'POST'/);
    assert.match(manager, /async function ensureBuiltinLutsRegistered\(options=\{\}\)/);
    assert.doesNotMatch(manager, /registryBuiltinHiddenIds/);
});

test('QIN-553：首次进入 LUT 库（统计胶囊与 lut 预设看板）触发内置登记', () => {
    assert.match(manager, /if\(category === 'lut'\)\{[\s\S]{0,160}await ensureBuiltinLutsRegistered\(\)/);
    assert.match(manager, /if\(board\.preset === 'lut' \|\| \(board\.kinds \|\| \[\]\)\.includes\('lut'\)\)\{[\s\S]{0,160}await ensureBuiltinLutsRegistered\(\)/);
});

test('QIN-621：全部 LUT 卡片（含内置）右上角统一渲染删除按钮，不再提供隐藏开关', () => {
    assert.doesNotMatch(manager, /registry-card-builtin-hide/);
    assert.doesNotMatch(manager, /data-registry-builtin-hidden/);
    assert.doesNotMatch(manager, /toggleRegistryBuiltinHidden/);
    assert.doesNotMatch(manager, /data-registry-reveal-builtin/);
    assert.match(manager, /data-registry-recycle="\$\{escapeAttr\(item\.id \|\| ''\)\}"/);
});
