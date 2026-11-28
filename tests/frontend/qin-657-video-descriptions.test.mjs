// [QIN-657] AI 分类设置迁移、镜头摘要读取与批量入口回归
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

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

function classes(...initial) {
  const values = new Set(initial);
  return { add: value => values.add(value), remove: value => values.delete(value), contains: value => values.has(value), toggle: (value, on) => on ? values.add(value) : values.delete(value) };
}

test('异步读取及切换平台后，增强下拉框解除禁用并显示当前模型', async () => {
  const nativePath = new URL('../../web/js/core/gw-native-select.js', import.meta.url);
  const imported = await import(nativePath.href);
  const native = imported.default || imported;
  const ids = ['assetAiProvider', 'assetAiModel', 'assetAiCaptionPrompt', 'assetAiClassifyPrompt', 'assetVlmPrompt', 'assetAiClassificationRule', 'assetAiAutoToggle', 'assetVlmAutoToggle', 'assetAiSettingsStatus', 'assetAiSave', 'assetVlmSave', 'assetAiRuleReset'];
  const nodes = Object.fromEntries(ids.map(id => [id, {
    id, value:'', disabled:false, textContent:'', classList:classes(), listeners:{}, attributes:{},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    setAttribute(key, value) { this.attributes[key] = value; },
    getAttribute(key) { return this.attributes[key]; },
  }]));
  for (const node of [nodes.assetAiProvider, nodes.assetAiModel]) {
    Object.assign(node, {
      disabled:true, options:[{text:'正在读取中'}], parentElement:{classList:classes('gw-select-shell')},
      replaceChildren() { this.options = []; },
      appendChild(option) { this.options.push(option); },
    });
    native.sync(node);
    assert.equal(node.parentElement.classList.contains('is-disabled'), true);
    assert.equal(node.parentElement.classList.contains('is-loading'), true);
  }
  let resolveProviders;
  const providers = new Promise(resolve => { resolveProviders = resolve; });
  const response = value => ({ok:true, json:async () => value});
  const ctx = vm.createContext({
    document:{getElementById:id => nodes[id], createElement:() => ({value:'', textContent:'', get text() { return this.textContent; }})},
    window:{GWNativeSelect:native}, localStorage:{getItem:() => null, setItem() {}},
    fetch:async url => url === '/api/providers' ? response(await providers) : response(url.includes('video-descriptions') ? {auto_enabled:false} : {}),
  });
  const script = [...settingsPage.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]).find(text => text.includes("const LOCAL_KEY = 'asset_manager_local_caption_settings_v1'"));
  assert.ok(script);
  vm.runInContext(script, ctx);
  resolveProviders({providers:[{id:'one', enabled:true, chat_models:['model-a']}, {id:'two', enabled:true, chat_models:['model-b']}]});
  await new Promise(resolve => setImmediate(resolve));
  for (const node of [nodes.assetAiProvider, nodes.assetAiModel]) {
    assert.equal(node.disabled, false);
    assert.equal(node.parentElement.classList.contains('is-disabled'), false);
    assert.equal(node.parentElement.classList.contains('is-loading'), false);
  }
  assert.equal(nodes.assetAiModel.value, 'model-a');
  nodes.assetAiProvider.value = 'two';
  nodes.assetAiProvider.listeners.change();
  assert.equal(nodes.assetAiModel.value, 'model-b');
  assert.equal(nodes.assetAiModel.parentElement.classList.contains('is-disabled'), false);
});

test('统一资产任务完成后读取单条详情并渲染新摘要与标签', async () => {
  class Element {
    constructor(tag='div') { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.textContent = ''; this.isConnected = true; }
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    remove() { if(this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
    setAttribute() {}
    querySelector(selector) {
      if(selector === '[data-vlm-video-description-list]') return this.children.find(child => 'vlmVideoDescriptionList' in child.dataset) || null;
      if(selector === '.asset-video-segments-panel') return this.host || null;
      return null;
    }
  }
  const host = new Element();
  const overlay = new Element();
  overlay.host = host;
  const old = {id:'asset-1', metadata:{video_segments:[{start_sec:0, end_sec:2}]}};
  const state = {open:true, source:'registry', id:'asset-1', index:0, items:[old]};
  const updated = {id:'asset-1', metadata:{video_segments:[{start_sec:0, end_sec:2, summary:'新画面', tags:['人物','奔跑']}]}};
  let releaseDetail;
  let detail = new Promise(resolve => { releaseDetail = resolve; });
  let merged = null;
  const ctx = vm.createContext({
    document:{createElement:tag => new Element(tag), querySelector:selector => selector === '.asset-detail-viewer' ? overlay : null},
    detailViewerState:state, detailViewerVideoItem:() => state.items[state.index], detailViewerItem:(source,id) => source === 'registry' && id === old.id ? old : null,
    detailMediaCanvasPersistId:item => item?.id || '', setStatus() {},
    fetch:async () => ({poll_hint:{url:'x'}}), apiJsonResponse:async value => await value,
    pollVideoDescriptionJob:async () => ({status:'succeeded', result:{generated:1}}),
    assetManagerApi:{getRegistryAsset:() => detail}, normalizeRegistryAsset:item => item,
    mergeRegistryAssetUpdate:item => { merged = item; state.items[0] = item; },
    formatDetailVideoTime:value => String(value), globalThis:{crypto:{randomUUID:() => 'test'}},
  });
  const helpers = extractBlock(preview, 'function videoDescriptionParts(', '\nasync function hydrateVideoDescriptions(');
  const startJob = extractBlock(preview, 'async function startVideoDescriptionJob(', '\nasync function copyVideoDescription(');
  vm.runInContext(`${helpers}\n${startJob}`, ctx);
  const button = {dataset:{videoDescriptionRun:'asset-1'}, disabled:false, isConnected:true};
  const pending = ctx.startVideoDescriptionJob(button);
  await new Promise(resolve => setImmediate(resolve));
  releaseDetail({asset:updated});
  await pending;
  assert.equal(merged, updated);
  const section = host.children.find(child => 'vlmVideoDescriptionList' in child.dataset);
  assert.ok(section);
  const allText = node => [node.textContent, ...node.children.flatMap(allText)];
  assert.ok(allText(section).includes('新画面'));
  assert.ok(allText(section).includes('标签：人物 · 奔跑'));
  assert.equal(button.disabled, false);
  detail = new Promise(resolve => { releaseDetail = resolve; });
  merged = null;
  const delayed = ctx.startVideoDescriptionJob(button);
  await new Promise(resolve => setImmediate(resolve));
  state.items = [{id:'asset-2'}];
  releaseDetail({asset:updated});
  await delayed;
  assert.equal(merged, null, '切换素材后迟到的详情不得写回');
  assert.equal(host.children.length, 1);
});

test('视频元数据迟到时，控制条获得时长后补挂镜头标记', () => {
  const markers = [];
  const track = {querySelectorAll:() => markers, appendChild:node => markers.push(node)};
  const progress = {max:'0', value:'0', dataset:{}, setAttribute() {}, closest:() => track};
  const description = {dataset:{assetId:'asset-1'}};
  const overlay = {querySelector:selector => selector === '[data-detail-video-progress]' ? progress : selector === '[data-vlm-video-description-list]' ? description : null};
  const controls = {
    dataset:{detailVideoFor:'asset-1'}, classList:classes(), setAttribute() {},
    querySelector:selector => selector === '[data-detail-video-progress]' ? progress : null,
    querySelectorAll:() => [], closest:() => overlay,
  };
  const item = {id:'asset-1', metadata:{video_segments:[{start_sec:2, summary:'画面', tags:['动作']}]}};
  const ctx = vm.createContext({
    document:{querySelector:() => controls, createElement:() => ({dataset:{}, style:{}, setAttribute() {}})},
    CSS:{escape:value => value}, detailViewerVideoItem:() => item,
    detailViewerVideoState:() => ({rate:1}), detailViewerState:{source:'registry'},
    detailMediaCanvasPersistId:value => value.id, detailVideoPoint:() => null,
    detailWritableStorageRoots:() => [], formatDetailVideoTime:value => String(value),
    detailTranscriptAt:() => ({start:0,end:3,text:'同时显示台词'}), detailTranscriptTime:value => String(value),
  });
  const helpers = extractBlock(preview, 'function videoDescriptionParts(', '\nasync function hydrateVideoDescriptions(');
  const sync = extractBlock(preview, 'function syncDetailVideoControls(', '\nfunction handleDetailVideoMediaEvent(');
  vm.runInContext(`${helpers}\n${sync}`, ctx);
  ctx.hydrateVideoDescriptionTimeline(overlay, item, item.metadata.video_segments);
  assert.equal(markers.length, 0);
  const video = {duration:10, currentTime:0, paused:true, volume:1, playbackRate:1, closest:() => ({dataset:{detailMediaCanvasNode:'asset-1'}, classList:classes()})};
  ctx.syncDetailVideoControls(video);
  assert.equal(markers.length, 1);
  assert.match(progress.title, /同时显示台词/, '镜头标记与台词提示应同时保留');
  assert.equal(markers[0].dataset.videoDescriptionSeek, '2');
  assert.match(markers[0].title, /画面/);
  ctx.syncDetailVideoControls(video);
  assert.equal(markers.length, 1, '重复的时间更新不重复创建标记');
});
