/**
 * QIN-582（GitHub #674）：资产库启动期项目计数延后与 5s 轮询时序。
 *
 * 票面 AC5：首屏就绪前不再逐个发 getRegistryAssets('project_id=…&limit=1')，
 * 计数最终仍刷新到 UI；5s 自动索引轮询只在首屏就绪后启动。
 * AC6：测试登记 node-files.txt 与洁净室分片。
 */
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {existsSync, readFileSync} from 'node:fs';
import test from 'node:test';

const MANAGER = readAssetManagerAggregate();
const NODE_FILES = readFileSync(new URL('./node-files.txt', import.meta.url), 'utf8');

test('AC5: 首屏关键路径不再 await 项目计数请求', () => {
    assert.doesNotMatch(
        MANAGER,
        /await refreshRegistryProjectCounts\(false\);/,
        'bootstrap 与列表刷新不得在首屏关键路径上等待 count 请求'
    );
    // 2 处：bootstrap 与列表刷新改为非阻塞调度；另 1 处在就绪标记内部放行排队任务。
    // 3 处：bootstrap、列表刷新两处改为非阻塞调度，加就绪标记内部放行排队任务。
    const scheduled = MANAGER.match(/scheduleRegistryProjectCounts\(\);/g) || [];
    assert.equal(scheduled.length, 3, '两处项目计数刷新都应改为非阻塞调度');
});

test('AC5: 未就绪只标脏；就绪后在空闲时段刷新一次', () => {
    const schedule = /function scheduleRegistryProjectCounts\(\)\s*\{([\s\S]*?)\n\}/.exec(MANAGER);
    assert.ok(schedule, '缺少 scheduleRegistryProjectCounts');
    assert.match(schedule[1], /registryProjectCountsDirty = true;/);
    assert.match(
        schedule[1],
        /if\(!registryFirstScreenReady \|\| registryProjectCountsScheduled\) return;/,
        '首屏未就绪或已有排队任务时不得发请求'
    );
    assert.match(schedule[1], /window\.requestIdleCallback\(run, \{timeout:2000\}\)/);
    assert.match(schedule[1], /window\.setTimeout\(run, 0\)/, '无 requestIdleCallback 时要有兜底');
    assert.match(schedule[1], /refreshRegistryProjectCounts\(true\)/, '计数到达后必须 render');
    assert.match(
        MANAGER,
        /function markRegistryFirstScreenReady\(\)\s*\{\s*registryFirstScreenReady = true;\s*if\(registryProjectCountsDirty\) scheduleRegistryProjectCounts\(\);\s*\}/,
        '就绪标记只放行已排队的计数刷新'
    );
});

test('AC5: 就绪标记在会话保存之后，5s 轮询仍只在其后启动', () => {
    assert.match(
        MANAGER,
        /saveRegistryBrowseSession\(\);\s*\/\/ 【QIN-582 AC5】[^\n]*\n\s*markRegistryFirstScreenReady\(\);\s*startRegistryAutomaticIndexPolling\(\);/,
        '保持 QIN-532 的「还原滚动 → 保存会话」序列，轮询仍在首屏收尾后启动'
    );
    assert.match(
        MANAGER,
        /window\.setInterval\(\(\) => pollRegistryAutomaticIndex\(\)\.catch\(\(\) => \{\}\),5000\)/
    );
    assert.match(
        MANAGER,
        /\.catch\(err => \{\s*markRegistryFirstScreenReady\(\);\s*setStatus\(err\.message \|\| '加载失败'\);\s*\}\);/,
        'loadAll 失败也要兜底标记，避免项目计数永久为 0'
    );
    const bootStart = MANAGER.indexOf('loadAll().then(async () =>');
    assert.ok(bootStart > 0, '引导入口必须存在');
    assert.equal(
        (MANAGER.slice(bootStart).match(/startRegistryAutomaticIndexPolling\(\);/g) || []).length,
        1,
        '轮询只在引导收尾处启动一次'
    );
});

test('AC5: 项目计数仍写入 Map 并触发渲染', () => {
    const refresh = /async function refreshRegistryProjectCounts\(renderNow = true\)\s*\{([\s\S]*?)\n\}/.exec(MANAGER);
    assert.ok(refresh, '缺少 refreshRegistryProjectCounts');
    assert.match(refresh[1], /registryProjectAssetCounts\.set\(proj\.id, total\)/);
    assert.match(refresh[1], /if\(renderNow\) render\(\);/);
});

test('AC6: 本票测试与洁净室分片已登记', () => {
    assert.match(
        NODE_FILES,
        /tests\/frontend\/qin-582-registry-boot-requests\.test\.mjs/,
        '新增前端测试必须登记 node-files.txt'
    );
    assert.ok(
        existsSync(new URL('../hygiene/fixtures/admissions/issue-674.json', import.meta.url)),
        '新增测试必须完成洁净室准入分片登记'
    );
});
