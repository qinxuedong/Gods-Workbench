import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const controller = readFileSync('web/js/controllers/production-controller.js', 'utf8');

test('生产页控制器不再包含演示目录或 proj-01 回落', () => {
  assert.doesNotMatch(controller, /DEMO_PREVIEW/);
  assert.doesNotMatch(controller, /\b(?:const|let|var)\s+demoProjectCatalog\b/);
  assert.doesNotMatch(controller, /demoProjectCatalog\s*\[/);
  assert.doesNotMatch(controller, /proj-01/);
  assert.match(controller, /params\.get\('project_id'\) \|\| localStorage\.getItem\('workspace_project_id'\) \|\| ''/);
});

test('未接入状态使用明确降级标记并清空静态制片示例', () => {
  assert.match(controller, /function renderNotIntegratedState\(\)/);
  assert.match(controller, /data-gw-degradation="not_integrated"/);
  assert.match(controller, /制片数据未接入/);
  assert.match(controller, /contextBusContainer/);
  assert.match(controller, /catalogEpisodePills/);
  assert.match(controller, /shotTrackList/);
});

test('无真实剧集时交互入口不创建本地伪造数据', () => {
  assert.match(controller, /state\.currentEpisode = null/);
  assert.match(controller, /state\.shotList = \[\]/);
  assert.match(controller, /if \(!state\.currentEpisode\) return/);
  assert.match(controller, /if \(!state\.currentEpisode \|\| !state\.currentEpisode\.scenes\)/);
});
