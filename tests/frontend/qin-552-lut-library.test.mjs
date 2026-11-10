import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const manager = readAssetManagerAggregate();

test('QIN-552：LUT 库只匹配 .cube，并在资产分类与统计中单列', () => {
    assert.match(manager, /\{id:'lut', name:'LUT库',[^\n]*kinds:\['lut'\], extensions:\['cube'\]\}/);
    assert.match(manager, /lut:new Set\(\['cube'\]\)/);
    // 【QIN-580】LUT 保留顶栏导航入口（排在「提示词」之后），但不出统计数值卡、不计入「资产总量」；
    // 分类口径（lut / .cube）本身不变。
    assert.doesNotMatch(manager, /id:'lut', name:'LUT',[^\n]*registryCategoryCount\('lut'\)/);
    assert.match(manager, /\{id:'lut', name:'LUT', nav:true, icon:'palette'\}/);
    assert.match(manager, /lut:'LUT'/);
    assert.match(manager, /cube:'Cube LUT 色彩查找表（\.cube）'/);
    assert.match(manager, /const isLut = String\(item\.kind \|\| item\.media_type \|\| item\.type \|\| ''\)\.toLowerCase\(\) === 'lut'\s*\|\| registryAssetExtension\(item\)\.toLowerCase\(\) === 'cube';/);
    assert.match(manager, /const format = isLut \? 'LUT'/);
});

test('QIN-552：索引失败提示可读，类型图标使用调色板', () => {
    assert.match(manager, /LUT_CUBE_INVALID:'LUT 文件格式无效，请检查文件头和采样数据'/);
    assert.match(manager, /LUT_CUBE_TOO_LARGE:'LUT 文件超过 64 MiB 上限'/);
    assert.match(manager, /lut:'palette'/);
});
