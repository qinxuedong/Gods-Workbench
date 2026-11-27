// [QIN-657] AI 分类设置迁移、镜头摘要读取与批量入口回归
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsPage = readFileSync(new URL('../../web/pages/settings.html', import.meta.url), 'utf8');
const assetSettings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');
const preview = readFileSync(new URL('../../web/js/modules/asset-manager/preview.js', import.meta.url), 'utf8');
const assetsController = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');

function extractBlock(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `未能提取代码块: ${startMarker}`);
  return source.slice(start, end);
}

test('AC1: AI 分类与 VLM 设置集中在智能检索页，旧弹窗不再提供重复编辑器', () => {
  assert.match(settingsPage, /data-panel="smart-search"[\s\S]*?data-smart-media-ai/);
  assert.match(settingsPage, /id="assetVlmAutoToggle"[^>]*aria-pressed="false"/);
  assert.match(settingsPage, /asset_manager_local_caption_settings_v1/);
  assert.match(settingsPage, /API 与密钥配置/);
  assert.match(assetSettings, /AI 分类设置已迁至「设置 → 智能检索与媒体分析」/);
  assert.doesNotMatch(assetSettings, /function renderAssetAiSettingsBody\(/);
});

test('AC2: 根目录菜单与视频详情均提交同一个受控后台任务端点', () => {
  assert.match(assetsController, /id: 'video-descriptions', attr: 'data-asset-organize-video-descriptions', label: '批量镜头描述与打标'/);
  assert.match(assetsController, /body:JSON\.stringify\(\{root_id:target\.rootId\}\)/);
  assert.match(preview, /body:JSON\.stringify\(\{asset_id:assetId\}\)/);
  assert.match(preview, /\/api\/asset-registry\/video-descriptions\/jobs/);
  assert.match(preview, /assetManagerApi\.getWorkspaceJob\(jobId\)/);
  assert.doesNotMatch(preview, /\/api\/asset-registry\/workspace-jobs/);
});

test('AC3: 分镜描述以文本节点安全展示，支持复制与时间轴摘要提示', () => {
  const renderList = extractBlock(preview, 'function renderVideoDescriptionList(', '\nfunction hydrateVideoDescriptionTimeline(');
  const timeline = extractBlock(preview, 'function hydrateVideoDescriptionTimeline(', '\nasync function hydrateVideoDescriptions(');
  assert.match(renderList, /summary\.textContent = parts\.summary/);
  assert.match(renderList, /tags\.textContent = parts\.tags/);
  assert.match(renderList, /dataset\.videoDescriptionCopy = ''/);
  assert.match(timeline, /parts\.summary/);
  assert.match(timeline, /parts\.tags/);
  assert.match(timeline, /dataset\.videoDescriptionSeek = String\(start\)/);
  assert.match(preview, /navigator\.clipboard\.writeText\(text\)/);
});
