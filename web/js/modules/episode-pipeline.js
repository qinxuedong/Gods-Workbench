/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

(function () {
  'use strict';

  const {api, escapeHtml: esc, dateTime} = Workspace;
  const q = selector => document.querySelector(selector);
  const STAGES = [
    {key: 'script', label: '剧本生成', display: '剧本生成', modelKind: 'text', prompt: 'episode_script_planning'},
    {key: 'assets', label: '资产生成', display: '资产生成', modelKind: 'text', modelLabel: '图片模型（卡片生图）', prompt: 'episode_asset_character', promptItems: ['episode_asset_character', 'episode_asset_scene', 'episode_asset_prop']},
    {key: 'video', label: '视频生成', display: '分镜脚本', modelKind: 'text', prompt: 'episode_prompt_segment_planning', promptItems: ['episode_prompt_segment_planning', 'episode_prompt_segment_split', 'episode_prompt_segment_review']},
    {key: 'audio_compose', label: '声音&合成', display: '视频脚本', modelKind: 'text', prompt: 'episode_prompt_seedance_scene'},
  ];
  const SCRIPT_MODES = [
    {key: 'planning', label: '剧情描述生成剧本', prompt: 'episode_script_planning'},
    {key: 'writing', label: '图片生成连续性剧本', prompt: 'episode_script_writing'},
    {key: 'polish', label: '剧本优化重生系统', prompt: 'episode_script_polish'},
  ];
  const SCRIPT_GENRES = ['成长 / 剧情', '悬疑 / 惊悚', '科幻 / 赛博', '奇幻 / 冒险', '都市 / 职业', '热血 / 逆袭'];
  const SCRIPT_STYLES = ['电影感写实', '赛博朋克霓虹', '黑白胶片质感', '暗黑克苏鲁', '动漫二次元', '国风水墨'];
  const SCRIPT_DURATIONS = ['1分钟 (01:00)', '2分钟 (02:00)', '3分钟 (03:00)', '5分钟 (05:00)', '30秒 (00:30)'];
  const SCRIPT_PACINGS = ['0.75x', '1x', '1.25x', '1.5x', '2x'];
  function checksum(text) { let hash = 0; const str = String(text || ''); for (let i = 0; i < str.length; i++) { hash = ((hash << 5) - hash) + str.charCodeAt(i); hash |= 0; } return Math.abs(hash).toString(16).toUpperCase().padStart(6, '0').slice(-6); }
  const SCRIPT_REVIEW_DIMENSIONS = ['StoryProgress', 'CharacterEmotion', 'DialogueQuality', 'PaceControl', 'Readability', 'AntiAI', 'FormatCompliance'];
  const SCRIPT_DIMENSION_LABELS = {StoryProgress: '故事推进', CharacterEmotion: '角色与情感', DialogueQuality: '对白质量', PaceControl: '节奏控制', Readability: '追读力', AntiAI: '去 AI 味', FormatCompliance: '格式规范'};
  const SCRIPT_REVIEW_KEYS = ['score', 'status', 'overallVerdict', 'problems', 'suggestions', 'priority', 'rewriteExample', 'revisionPath', 'surgeryTable', 'dimensions'];
  const STORYBOARD_REVIEW_DIMENSIONS = ['NarrativeClarity', 'ShotContinuity', 'VisualExecutability', 'RhythmControl', 'AssetConsistency'];
  const STORYBOARD_DIMENSION_LABELS = {NarrativeClarity: '叙事清晰度', ShotContinuity: '镜头连续性', VisualExecutability: '可执行性', RhythmControl: '节奏控制', AssetConsistency: '资产一致性'};
  const STORYBOARD_REVIEW_KEYS = ['score', 'status', 'summary', 'issues', 'suggestions', 'priority', 'dimensions'];
  const STORYBOARD_REVISION_FIELDS = ['duration', 'camera', 'scene', 'movement', 'transition', 'beats', 'content', 'title', 'shotType'];
  const STORYBOARD_OUTLINE_KEYS = ['coreConflict', 'protagonistMotivation', 'informationGain', 'fiveActs', 'sceneQuality', 'shots'];
  const STORYBOARD_SHOT_KEYS = ['index', 'title', 'scriptContent', 'shotType', 'keyBeats'];
  const params = new URLSearchParams(location.search);
  const state = {projects: [], libraries: [], providers: [], projectId: params.get('project_id') || localStorage.getItem('workspace_project_id') || '', pipelines: [], selected: null, selectedPipelineId: params.get('pipeline_id') || '', activeStep: STAGES.some(item => item.key === params.get('step')) ? params.get('step') : 'script', busy: false, toastTimer: 0, uploadAssetId: '', uploadPipelineId: '', assetGroups: {}, focusedAsset: null, detailReturnFocus: null, pending: {}, assetRuns: {}, scriptEditorMode: 'preview', storyboardSubStep: 1, videoActorId: '', videoIdentityReadGeneration: 0};
  const WORKSPACE_KEY = 'gwb_episode_workspace_v2';
  const SCRIPT_ASSET_KEY = 'gwb_script_assets_v1';
  const ASSET_POLL_INTERVAL_MS = 2500;
  const ASSET_AUTO_STOP_MS = 120000;
  const videoPolls = new Map();
  const videoRequests = new Set();
  const VIDEO_CONTEXT_STORAGE_PREFIX = 'gwb_video_production_context_v1';
  const videoContext = {scopeKey: '', projectId: '', pipelineId: '', actorId: '', canvasId: '', entityId: '', canvases: [], nodes: [], loading: '', error: '', generation: 0, controller: null};

  const DEFAULT_ASSETS_DEMO = [
    { id: 'asset-01', name: '林浩', type: 'character', prompt: '根据剧本建立“林浩”的统一视觉设定，锁定面部五官与战术风衣材质，保持后续镜头连续性。', status: 'running', preview: 'https://lh3.googleusercontent.com/aida-public/AB6AXuD05s7fKU3W0sxDtoNaPSvqhxs-sAyO3oNnRtpvgYnc72ryANA_h8I0BFAzb4B34cYDLzXlYt66FDdfHfIbjvOZvxb3G5Xg2O697SzeMXPmKxeo180L6jsYObIKp9OswOqPzSxXyjPNM7AwXZB29tLWpOoxlyeUW-mskOJ98PHrp6k6-ipnyJ04gizllKggww21os_o8bVcmgRZ62uex5rZN7mG5YNp-kL7R7Zn63wP-hYGlO0Qf_2A' },
    { id: 'asset-02', name: '陈默', type: 'character', prompt: '根据剧本建立“陈默”的反派统一视觉设定，暗银色工装与义肢接口，保持后续镜头连贯。', status: 'pending', preview: '' },
    { id: 'asset-03', name: '韩锋', type: 'character', prompt: '根据剧本建立电竞战队队长“韩锋”设定，强化坚毅神态与定制团队战服细节。', status: 'pending', preview: '' },
    { id: 'asset-04', name: '网咖训练室', type: 'scene', prompt: '根据剧本建立“网咖训练室”的统一视觉设定，霓虹暗光、机房线缆阵列与主备服务器布局。', status: 'pending', preview: '' },
    { id: 'asset-05', name: '比赛舞台', type: 'scene', prompt: '根据剧本建立“总决赛比赛舞台”的统一视觉设定，环形穹顶屏幕，震撼万人观众席。', status: 'pending', preview: '' },
    { id: 'asset-06', name: '战术定制鼠标', type: 'prop', prompt: '根据剧本提取的特写道具：磨损碳纤维外壳、自制宏按键与裂痕指示灯。', status: 'pending', preview: '' },
    { id: 'asset-07', name: '深空避难所天台', type: 'scene', prompt: '金属网格地面、重型通风管排气，暴雨夜幕下的巨型霓虹全息广告倒影。', status: 'pending', preview: '' },
    { id: 'asset-08', name: '神经义眼扫描器', type: 'prop', prompt: '钛合金外壳战术目镜，边缘带有精密刻度与微光数据流接口。', status: 'pending', preview: '' },
    { id: 'asset-09', name: '战术数据芯片', type: 'prop', prompt: '泛着琥珀金光的微晶数据芯片，表面雕刻有加密电路与序列号。', status: 'pending', preview: '' },
    { id: 'asset-10', name: '地下竞技场过道', type: 'scene', prompt: '斑驳水泥墙面，涂鸦与昏黄警示灯交错，狭窄逼仄的工业通道。', status: 'pending', preview: '' },
    { id: 'asset-11', name: '裁判长', type: 'character', prompt: '身着笔挺制式黑色风衣，佩戴全息战术耳麦的冷峻中年裁判长。', status: 'pending', preview: '' },
    { id: 'asset-12', name: '主控中继终端', type: 'other', prompt: '三联屏折叠便携式终端机，正在高速跑动十六进制内核状态码。', status: 'pending', preview: '' },
    { id: 'asset-13', name: '备用能量液罐', type: 'other', prompt: '高压铝制荧光液压罐，装填淡蓝色战术镇静补充剂。', status: 'pending', preview: '' }
  ];

  function statusText(status) { return ({queued: '待开始', running: '执行中', cancel_requested: '停止中', succeeded: '已完成', failed: '失败', canceled: '已停止', interrupted: '已中断'}[String(status || '')] || '未开始'); }
  function statusClass(status) { return String(status || 'queued').replace(/[^a-z_]/g, ''); }
  function toast(message, error = false) { const el = q('#episodeToast'); if (!el) return; el.textContent = String(message || '操作完成'); el.className = `episode-toast visible${error ? ' error' : ''}`; window.clearTimeout(state.toastTimer); state.toastTimer = window.setTimeout(() => { el.className = 'episode-toast'; }, error ? 6200 : 5200); }
  function libraryOptions() { const select = q('#promptLibrary'); if (!select) return; const preferred = state.libraries.find(item => item.id === 'episode') || state.libraries.find(item => item.id === 'system'); select.innerHTML = state.libraries.filter(item => item.id === 'episode' || item.id === 'system').map(item => `<option value="${esc(item.id)}"${item.id === preferred?.id ? ' selected' : ''}>${esc(item.name || item.id)}</option>`).join(''); select.disabled = !state.libraries.length; }
  function currentPipeline() { return state.selected || state.pipelines[0] || null; }
  function stageFor(pipeline, key) { return pipeline?.stages?.find(item => item.stage === key) || {stage: key, label: STAGES.find(item => item.key === key)?.label || key, status: 'queued', result: {}}; }
  function scriptMode(pipeline) { const data = readWorkspace(pipeline); return SCRIPT_MODES.find(item => item.key === data.scriptMode) || SCRIPT_MODES[0]; }
  function stagePromptItem(pipeline, stage) { const spec = STAGES.find(item => item.key === stage?.stage); return stage?.stage === 'script' ? scriptMode(pipeline).prompt : stage?.prompt_item_id || spec?.prompt || ''; }
  // 阶段初始上下文可能为空对象，须保留流水线已有的稳定实体关联。
  function contextParams(pipeline, stage) { const context = {...(pipeline?.production_context || {}), ...(stage?.production_context || {})}; const spec = STAGES.find(item => item.key === stage?.stage); const promptItemId = stagePromptItem(pipeline, stage); return {project_id: context.project_id || state.projectId, entity_id: context.entity_id || '', canvas_id: context.canvas_id || '', episode_pipeline_id: pipeline?.pipeline_id || '', episode_stage: stage?.stage || '', episode_stage_job_id: stage?.job_id || '', episode_prompt_library_id: pipeline?.prompt_library_id || 'episode', episode_prompt_item_id: promptItemId, episode_prompt_item_ids: (spec?.promptItems || [promptItemId]).filter(Boolean).join(',')}; }
  function promptItem(pipeline, itemId) { const library = state.libraries.find(item => item.id === (pipeline?.prompt_library_id || 'episode')) || state.libraries.find(item => item.id === 'episode'); return library?.items?.find(item => item.id === itemId) || null; }
  function promptDocument(pipeline, itemId) { const item = promptItem(pipeline, itemId); const negative = String(item?.negative || '').trim(); const legacyUserTemplate = item?.params?.source === 'legacy-code-template' ? negative : ''; return {name: String(item?.name || itemId || '剧集提示词'), positive: String(item?.positive || '').trim(), negative: legacyUserTemplate ? '' : negative, userTemplate: legacyUserTemplate}; }
  function promptMessage(documentPrompt, message) { const template = String(documentPrompt?.userTemplate || '').trim(); const content = String(message || '').trim(); if (!template) return content; return template.includes('{{message_1_content}}') ? template.replace(/\{\{message_1_content\}\}/g, content) : `${template}\n\n${content}`; }
  function scriptGenerationContext(data, mode) { return {script_mode: mode.key, output_mode: mode.label, genre: String(data.scriptGenre || ''), visual_style: String(data.scriptStyle || ''), episode_duration: String(data.scriptDuration || ''), pacing: String(data.scriptPacing || '')}; }
  function scriptGenerationSettingsText(context) { return `当前生成参数（必须纳入本次输出，不得忽略）：\n- 题材预设：${context.genre}\n- 视听风格：${context.visual_style}\n- 单集时长：${context.episode_duration}\n- 节奏：${context.pacing}\n- 输出模式：${context.output_mode}`; }
  function promptSource(pipeline, stage) { const doc = promptDocument(pipeline, stagePromptItem(pipeline, stage)); return `${state.libraries.find(item => item.id === (pipeline?.prompt_library_id || 'episode'))?.name || '当前项目剧集提示词库'} · ${doc.name}`; }
  const SCRIPT_ARCHITECT_ROUTE = '/static/pages/workshop.html?step=script&agent=script-03';
  const SCRIPT_ARCHITECT_AGENT_ID = 'script-03';
  function auraAgentFor(stageKey) { return stageKey === 'script' || stageKey === 'video' ? 'script-03' : stageKey === 'audio_compose' ? 'vox-04' : stageKey === 'assets' ? 'flux-02' : 'aura-01'; }
  function auraOperation(stageKey, itemId) { const stage = STAGES.find(item => item.key === stageKey); return `${stage?.display || stageKey} · ${itemId || '文本模型执行'}`; }
  function auraScope(pipeline, stageKey) { const stage = stageFor(pipeline, stageKey); const context = stage?.production_context || pipeline?.production_context || {}; return {project_id: context.project_id || state.projectId || '', pipeline_id: pipeline?.pipeline_id || '', stage: stageKey, job_id: stage?.job_id || ''}; }
  function syncAuraScope(pipeline) { if (pipeline && window.AuraTraceBus?.setScope) window.AuraTraceBus.setScope({project_id: state.projectId || pipeline.project_id || '', pipeline_id: pipeline.pipeline_id || ''}); }
  function auraResultSummary(text) { const value = String(text || ''); return `模型已返回 ${value.length} 字符，校验和 ${checksum(value)}`; }
  function auraErrorType(error) { const name = String(error?.name || ''); return ['AbortError', 'TimeoutError', 'TypeError', 'SyntaxError'].includes(name) ? name : 'Error'; }
  function auraTrace(stageKey, tag, summary, detail, operation, status = '', pipeline = null) {
    if (!window.AuraTraceBus?.publish) return;
    const agentId = auraAgentFor(stageKey);
    window.AuraTraceBus.publish({tag, summary, detail, agentId, route: agentId === 'script-03' ? SCRIPT_ARCHITECT_ROUTE : 'episode-pipeline', operation: operation || auraOperation(stageKey, ''), stage: stageKey, status, scope: auraScope(pipeline, stageKey)});
  }
  function readWorkspace(pipeline) { if (!pipeline) return {}; try { const raw = JSON.parse(localStorage.getItem(`${WORKSPACE_KEY}:${pipeline.pipeline_id}`) || '{}'); return raw && typeof raw === 'object' ? raw : {}; } catch (_) { return {}; } }
  function writeWorkspace(pipeline, value) { if (!pipeline) return; try { localStorage.setItem(`${WORKSPACE_KEY}:${pipeline.pipeline_id}`, JSON.stringify(value || {})); } catch (_) {} }
  function readScriptAssets() { try { const value = JSON.parse(localStorage.getItem(SCRIPT_ASSET_KEY) || '[]'); return Array.isArray(value) ? value : []; } catch (_) { return []; } }
  function archiveScriptAsset(pipeline, text) { const content = String(text || '').trim(); if (!pipeline || !content) return; const assets = readScriptAssets(); if (assets.some(item => item.pipelineId === pipeline.pipeline_id && item.content === content)) return; assets.unshift({id: `script:${pipeline.pipeline_id}:${Date.now()}`, pipelineId: pipeline.pipeline_id, projectId: state.projectId, title: `${pipeline.title || '未命名剧集'} · 剧本`, name: `${pipeline.title || '未命名剧集'}.md`, kind: 'script', format: 'md', content, createdAt: Date.now()}); try { localStorage.setItem(SCRIPT_ASSET_KEY, JSON.stringify(assets.slice(0, 100))); } catch (_) {} }
  function stageResultText(stage) { return String(stage?.result?.output_text || '').trim(); }
  function localData(pipeline) { const value = readWorkspace(pipeline); value.scriptMode = SCRIPT_MODES.some(item => item.key === value.scriptMode) ? value.scriptMode : SCRIPT_MODES[0].key; value.scriptDraft = String(value.scriptDraft ?? pipeline?.seed ?? ''); value.scriptOutput = String(value.scriptOutput || ''); value.scriptGenre = String(value.scriptGenre || '成长 / 剧情'); value.scriptStyle = String(value.scriptStyle || '电影感写实'); value.scriptDuration = String(value.scriptDuration || '1分钟 (01:00)'); value.scriptPacing = SCRIPT_PACINGS.includes(String(value.scriptPacing || '')) ? String(value.scriptPacing) : '1.25x'; const rawAssets = Array.isArray(value.assets) && value.assets.length ? value.assets : DEFAULT_ASSETS_DEMO; const usedAssetIds = new Set(); value.assets = rawAssets.map((item, index) => ({...item, id: uniqueAssetId(item, index, usedAssetIds)})); if (value.assets.some((item, index) => item.id !== rawAssets[index]?.id)) writeWorkspace(pipeline, value); value.storyboard = value.storyboard && typeof value.storyboard === 'object' ? value.storyboard : {outline: '', shots: [], review: null}; value.storyboard.shots = Array.isArray(value.storyboard.shots) ? value.storyboard.shots : []; value.videoScripts = Array.isArray(value.videoScripts) ? value.videoScripts : []; value.history = Array.isArray(value.history) ? value.history : []; value.models = value.models && typeof value.models === 'object' ? value.models : {}; value.pending = value.pending && typeof value.pending === 'object' ? value.pending : {}; return value; }
  function remember(pipeline, data, type, payload) { data.history.unshift({type, at: Date.now(), ...payload}); data.history = data.history.slice(0, 80); writeWorkspace(pipeline, data); }
  function stageOutput(pipeline, stage) { const data = localData(pipeline); return stage.stage === 'script' && data.scriptOutput ? data.scriptOutput : stageResultText(stage); }
  function pendingFor(pipeline, key) { return Boolean(pipeline && state.pending[pipeline.pipeline_id]?.[key]); }
  function setPending(pipeline, key, value) { if (!pipeline) return; state.pending[pipeline.pipeline_id] = state.pending[pipeline.pipeline_id] || {}; if (value) state.pending[pipeline.pipeline_id][key] = value; else delete state.pending[pipeline.pipeline_id][key]; }
  function stageLoading(pipeline, stage) { const pendingKey = stage.stage === 'script' ? 'scriptReview' : stage.stage === 'video' ? 'storyboardReview' : stage.stage === 'audio_compose' ? 'videoScripts' : ''; const pending = pendingKey && pendingFor(pipeline, pendingKey); if (stage.status !== 'running' && stage.status !== 'cancel_requested' && !pending) return ''; const label = stage.status === 'cancel_requested' ? '停止请求已发送，正在结束当前任务…' : pending === 'review' ? '审核 agent 正在分析结果…' : pending === 'revision' ? '正在按审核意见重新生成…' : stage.stage === 'assets' ? '资产提取 agent 正在整理提示词…' : stage.stage === 'video' ? '分镜 agent 正在处理当前步骤…' : stage.stage === 'audio_compose' ? '视频脚本 agent 正在生成逐镜内容…' : '模型正在生成内容，等待后台返回…'; return `<div class="stage-loading" role="status"><span class="loading-spinner" aria-hidden="true"></span><span>${esc(label)}</span></div>`; }
  function inlineLoading(label = '后台处理中') { return `<span class="inline-loading" role="status"><span class="loading-spinner" aria-hidden="true"></span>${esc(label)}</span>`; }
  function actionMessage(target) { if (target.matches('[data-stage-start],[data-stage-retry]')) { const key = target.dataset.stageStart || target.dataset.stageRetry; return `正在执行：${STAGES.find(item => item.key === key)?.display || key}，已发送后台任务`; } if (target.matches('[data-stage-stop]')) return '正在停止当前阶段，等待任务状态确认'; if (target.matches('[data-script-review]')) return '正在执行：剧本审核，等待审核 agent 返回评分与意见'; if (target.matches('[data-revise-script]')) return '正在执行：按审核意见重新生成剧本'; if (target.matches('[data-assets-refresh]')) return '正在执行：重新提取角色、场景和道具资产'; if (target.matches('[data-asset-generate],[data-asset-detail-regenerate]')) return '正在执行：生成资产图片，等待图片模型返回'; if (target.matches('[data-outline-generate]')) return '正在执行：生成分镜规划大纲'; if (target.matches('[data-shots-generate]')) return '正在执行：拆分具体分镜'; if (target.matches('[data-storyboard-review]')) return '正在执行：分镜审核，等待审核官返回评分'; if (target.matches('[data-revise-storyboard]')) return '正在执行：按审核意见重新生成分镜'; if (target.matches('[data-video-scripts-generate]')) return '正在执行：生成详细视频制作脚本'; if (target.matches('[data-video-generate]')) return '正在执行：生成当前镜头视频'; if (target.matches('[data-complete-storyboard]')) return '正在保存：确认分镜并进入视频脚本'; if (target.matches('[data-complete-video-script]')) return '正在保存：确认视频脚本'; if (target.matches('[data-save-script]')) return '正在保存：当前剧本版本'; return ''; }
  function providerPreference(provider) { const id = String(provider?.id || '').toLowerCase(); const configured = provider?.has_key === true || id === 'codex' || id === 'gemini-cli'; if (provider?.primary && configured) return 0; if (configured && id !== 'modelscope') return 1; if (provider?.primary) return 2; if (configured) return 3; return id === 'modelscope' ? 5 : 4; }
  function modelOptions(kind) { const field = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models'; const options = []; const providers = state.providers.filter(provider => provider && provider.enabled !== false).slice(); if (kind === 'text') providers.sort((left, right) => providerPreference(left) - providerPreference(right)); providers.forEach(provider => (provider[field] || []).forEach(model => options.push({provider_id: String(provider.id), provider_name: String(provider.name || provider.id), model: String(model), label: String(provider.model_names?.[model] || model)}))); return options; }
  function selectedProviderModel(pipeline, stageKey, kindOverride) { const data = localData(pipeline); const spec = STAGES.find(item => item.key === stageKey) || {}; const options = modelOptions(kindOverride || spec.modelKind || 'text'); const saved = data.models[stageKey] || (stageKey === 'asset_image' ? data.models.assets : null); return options.find(item => item.provider_id === saved?.provider_id && item.model === saved?.model) || options[0] || {provider_id: '', model: ''}; }
  function modelPicker(pipeline, stage) { const spec = STAGES.find(item => item.key === stage.stage) || {}; const modelKind = stage.modelKind || spec.modelKind || 'text'; const options = modelOptions(modelKind); const selected = selectedProviderModel(pipeline, stage.stage, modelKind); const label = stage.modelLabel || (modelKind === 'image' ? '图片模型' : modelKind === 'video' ? '视频模型' : '文本模型'); return `<label class="stage-model-picker"><span>${esc(label)}</span><select data-stage-model="${esc(stage.stage)}" aria-label="选择${esc(label)}" ${options.length ? '' : 'disabled'}>${options.length ? options.map(item => `<option value="${esc(`${item.provider_id}:::${item.model}`)}"${item.provider_id === selected.provider_id && item.model === selected.model ? ' selected' : ''}>${esc(item.provider_name)} / ${esc(item.label)}</option>`).join('') : '<option>未配置模型</option>'}</select></label>`; }
  function selectedVideoModel(pipeline) { const render = selectedProviderModel(pipeline, 'video_render', 'video'); return render.provider_id ? render : selectedProviderModel(pipeline, 'audio_compose', 'video'); /* selectedProviderModel(pipeline, 'audio_compose', 'video') remains the persisted-model compatibility route. */ }
  function splitSeed(text) { return [...new Set(String(text || '').split(/[\n。！？；，,、.!?;]+/).map(value => value.trim()).filter(value => value.length >= 2))].slice(0, 16); }
  function classifyAsset(value, index) { if (/(人物|男|女|少年|女孩|老人|狗|猫|主角|角色)/.test(value) || index === 0) return 'character'; if (/(室内|室外|街|房|屋|学校|工地|森林|雨|夜|场景|车站)/.test(value) || index === 1) return 'scene'; if (/(门|伞|车|手机|箱|刀|书|杯|道具|项链|红绳)/.test(value) || index === 2) return 'prop'; return 'other'; }
  function assetTypeLabel(type) { return ({character: '角色', scene: '场景', prop: '道具', other: '其他'}[type] || '其他'); }
  function assetSlug(value) { return String(value || '').trim().toLowerCase().replace(/[^\w\u4e00-\u9fff]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 42) || 'asset'; }
  function uniqueAssetId(asset, index, used) { const base = String(asset.id || `asset-${assetSlug(asset.type)}-${assetSlug(asset.name)}-${index + 1}`); let id = base; let suffix = 2; while (used.has(id)) id = `${base}-${suffix++}`; used.add(id); return id; }
  function assetDocumentReady(pipeline, data = localData(pipeline)) { return Boolean(data.assets.length || stageResultText(stageFor(pipeline, 'assets'))); }
  function pendingAssetImages(pipeline, data = localData(pipeline)) { return data.assets.filter(item => item.status !== 'ready' && !item.preview).length; }
  function parseJson(text) { const raw = String(text || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, ''); try { return JSON.parse(raw); } catch (_) { const match = raw.match(/\{[\s\S]*\}|\[[\s\S]*\]/); try { return match ? JSON.parse(match[0]) : null; } catch (__) { return null; } } }
  function outlineFields(value) { const parsed = parseJson(value); const source = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}; const fiveActs = Array.isArray(source.fiveActs) ? source.fiveActs.slice(0, 5).map(item => String(item || '')) : []; while (fiveActs.length < 5) fiveActs.push(''); return {coreConflict: String(source.coreConflict || ''), protagonistMotivation: String(source.protagonistMotivation || ''), informationGain: String(source.informationGain || ''), fiveActs, sceneQuality: String(source.sceneQuality || ''), shots: Array.isArray(source.shots) ? source.shots : []}; }
  function outlineIsReady(value) { try { normalizeStoryboardOutline(value); return true; } catch (_) { return false; } }
  function storyboardSource(pipeline, data = localData(pipeline)) { return data.scriptOutput || stageResultText(stageFor(pipeline, 'script')) || data.scriptDraft || pipeline?.seed || ''; }
  function exactKeys(value, keys) { return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join('|') === keys.slice().sort().join('|'); }
  function requiredChineseText(value, field) { if (typeof value !== 'string' || !value.trim() || !/[\u4e00-\u9fff]/.test(value)) throw new Error(`分镜规划返回格式不正确：${field} 必须是非空中文文本`); return value.trim(); }
  function normalizeStoryboardOutline(text) { const raw = String(text || '').trim(); if (!raw || raw.includes('```')) throw new Error('分镜规划返回格式不正确：只能返回裸 JSON，不能使用 Markdown 代码块'); let parsed; try { parsed = JSON.parse(raw); } catch (_) { throw new Error('分镜规划返回格式不正确：返回内容不是合法 JSON'); } if (!exactKeys(parsed, STORYBOARD_OUTLINE_KEYS)) throw new Error(`分镜规划返回格式不正确：顶层字段必须严格为 ${STORYBOARD_OUTLINE_KEYS.join('、')}`); if (!Array.isArray(parsed.fiveActs) || parsed.fiveActs.length !== 5) throw new Error('分镜规划返回格式不正确：fiveActs 必须正好包含 5 条推进'); if (!Array.isArray(parsed.shots) || !parsed.shots.length || parsed.shots.length > 24) throw new Error('分镜规划返回格式不正确：shots 必须包含 1-24 个镜头'); const shots = parsed.shots.map((shot, index) => { if (!exactKeys(shot, STORYBOARD_SHOT_KEYS)) throw new Error(`分镜规划返回格式不正确：shots[${index}] 字段必须严格为 ${STORYBOARD_SHOT_KEYS.join('、')}`); if (shot.index !== index) throw new Error(`分镜规划返回格式不正确：shots[${index}].index 必须为 ${index}`); const shotType = String(shot.shotType || '').trim().toUpperCase(); if (!['A', 'B', 'C', 'D'].includes(shotType)) throw new Error(`分镜规划返回格式不正确：shots[${index}].shotType 只能是 A、B、C、D`); if (!Array.isArray(shot.keyBeats) || shot.keyBeats.length < 2 || shot.keyBeats.some(item => typeof item !== 'string' || !item.trim() || !/[\u4e00-\u9fff]/.test(item))) throw new Error(`分镜规划返回格式不正确：shots[${index}].keyBeats 至少包含 2 条可执行中文短节拍`); return {index, title: requiredChineseText(shot.title, `shots[${index}].title`), scriptContent: requiredChineseText(shot.scriptContent, `shots[${index}].scriptContent`), shotType, keyBeats: shot.keyBeats.map(item => item.trim())}; }); return {coreConflict: requiredChineseText(parsed.coreConflict, 'coreConflict'), protagonistMotivation: requiredChineseText(parsed.protagonistMotivation, 'protagonistMotivation'), informationGain: requiredChineseText(parsed.informationGain, 'informationGain'), fiveActs: parsed.fiveActs.map((item, index) => requiredChineseText(item, `fiveActs[${index}]`)), sceneQuality: requiredChineseText(parsed.sceneQuality, 'sceneQuality'), shots}; }
  function parseAssets(text) { const parsed = parseJson(text); let list = Array.isArray(parsed) ? parsed : parsed?.assets; if (!Array.isArray(list) && parsed && typeof parsed === 'object') list = Object.entries(parsed).flatMap(([type, values]) => Array.isArray(values) ? values.map(value => ({...(typeof value === 'object' ? value : {name: value}), type})) : []); if (!Array.isArray(list)) list = splitSeed(text).map(name => ({name, type: ''})); return list.map((item, index) => { const name = String(item?.name || item?.title || item?.character || item?.scene || item?.prop || '').trim(); return {id: String(item?.id || `asset-draft-${index + 1}`), name: name.slice(0, 100) || `资产 ${index + 1}`, type: ['character', 'scene', 'prop', 'other'].includes(item?.type) ? item.type : classifyAsset(name, index), prompt: String(item?.prompt || item?.description || `根据剧本建立“${name}”的统一视觉设定，保持后续镜头连续。`).trim(), status: 'pending', preview: '', assetId: ''}; }).filter(item => item.name).slice(0, 24); }
  function shotTitle(value, index) { const text = String(value || '').replace(/\s+/g, ' ').slice(0, 38); return text || `镜头 ${String(index + 1).padStart(2, '0')}`; }
  function parseShots(text, fallbackSource) { const parsed = parseJson(text); const list = Array.isArray(parsed) ? parsed : parsed?.shots || parsed?.storyboard; const raw = Array.isArray(list) ? list : splitSeed(text || fallbackSource).map(content => ({content})); return raw.map((item, index) => ({id: String(item?.id || `shot-${index + 1}`), title: shotTitle(item?.title || item?.name || item?.scriptContent || item?.content, index), duration: Number(item?.durationSec || item?.duration || 8), shotType: String(item?.shotType || item?.shot_type || ['A', 'B', 'B', 'C', 'D'][index % 5]), camera: String(item?.cameraPosition || item?.camera || item?.angle || '中景'), scene: String(item?.shotSize || item?.scene || '根据剧本设定'), movement: String(item?.movement || '固定'), transition: String(item?.transitionIntent || item?.transition || '硬切'), beats: Array.isArray(item?.keyBeats) ? item.keyBeats.join('；') : String(item?.beats || item?.beat || item?.scriptContent || item?.content || ''), content: String(item?.scriptContent || item?.content || item?.description || item?.beats || '')})).filter(item => item.content || item.beats).slice(0, 24); }
  function storyboardRevisionChanged(previous, next) { if (!Array.isArray(previous) || !Array.isArray(next) || previous.length !== next.length) return false; return next.some((shot, index) => { const before = previous.find(item => item.id === shot.id) || previous[index] || {}; return STORYBOARD_REVISION_FIELDS.some(field => String(before[field] ?? '') !== String(shot[field] ?? '')); }); }
  function parseVideoScripts(text, shots) { const parsed = parseJson(text); const list = Array.isArray(parsed) ? parsed : parsed?.scripts || parsed?.shots; if (Array.isArray(list)) return list.map((item, index) => ({id: String(item?.id || shots[index]?.id || `shot-${index + 1}`), shotId: String(item?.shotId || shots[index]?.id || `shot-${index + 1}`), title: String(item?.title || shots[index]?.title || `镜头 ${index + 1}`), prompt: String(item?.prompt || item?.positive || item?.content || ''), negative: String(item?.negative || item?.negative_prompt || ''), assets: String(item?.assets || '{{character:主角}}, {{scene:当前场景}}, {{prop:关键道具}}')})); return shots.map(shot => ({id: shot.id, shotId: shot.id, title: shot.title, prompt: shot.content, negative: 'deformed hands, extra fingers, inconsistent character, flickering, text, watermark', assets: '{{character:主角}}, {{scene:当前场景}}, {{prop:关键道具}}'})); }
  function statusChip(status) { return `<span class="status ${statusClass(status)}"><i aria-hidden="true"></i>${esc(statusText(status))}</span>`; }
  function stageHeading(pipeline, stage, index) { const spec = STAGES[index]; return `<div class="stage-heading"><div><div class="stage-kicker">步骤 ${String(index + 1).padStart(2, '0')} / ${esc(spec.display)}</div><h3 class="sr-only">${esc(spec.label)}</h3><h2>${esc(spec.display)}</h2><p>提示词链路：${esc(promptSource(pipeline, stage))}</p></div><div class="stage-heading-tools">${modelPicker(pipeline, stage)}${statusChip(stage.status)}</div></div>${stageLoading(pipeline, stage)}`; }
  function stageActions(pipeline, stage, index, extra = '') { const previous = index ? stageFor(pipeline, STAGES[index - 1].key) : null; const documentReady = stage.stage === 'video' && assetDocumentReady(pipeline); const previousReady = !previous || previous.status === 'succeeded' || documentReady; const canStart = (stage.status === 'queued' || stage.status === 'succeeded' || ['failed', 'canceled', 'interrupted'].includes(stage.status)) && previousReady; const startText = index === 0 ? '开始生成剧本' : index === 1 ? '根据剧本生成资产' : index === 2 ? '生成分镜大纲' : '根据分镜生成视频脚本'; const running = ['running', 'cancel_requested', 'recovering'].includes(stage.status); const stop = stage.stage === 'assets' && running ? `<button class="btn danger" type="button" data-stage-stop="${esc(stage.stage)}" data-pipeline-id="${esc(pipeline.pipeline_id)}" data-stage-version="${esc(stage.version || '')}" ${stage.status === 'cancel_requested' ? 'disabled' : ''}>${stage.status === 'cancel_requested' ? '停止中' : '停止任务'}</button>` : ''; const retry = canStart ? `<button class="btn${stage.status === 'queued' ? ' primary' : ''}" type="button" data-stage-${stage.status === 'queued' ? 'start' : 'retry'}="${esc(stage.stage)}" data-pipeline-id="${esc(pipeline.pipeline_id)}">${esc(stage.status === 'queued' ? startText : '重新执行')}</button>` : ''; return `<div class="stage-actions">${retry}${stop}${extra}</div>`; }
  function reviewMarkup(review, kind = 'script') {
    if (!review) return '<div class="review-empty">尚未送审，生成结果后可提交审核 agent。</div>';
    const reviseAttr = kind === 'script' ? 'data-revise-script' : 'data-revise-storyboard';
    const problemItems = Array.isArray(review.problems) && review.problems.length ? review.problems : review.issues;
    const problems = Array.isArray(problemItems) && problemItems.length ? problemItems : ['模型未返回主要问题。'];
    const suggestions = Array.isArray(review.suggestions) && review.suggestions.length ? review.suggestions : ['模型未返回可执行修改意见。'];
    const priorities = Array.isArray(review.priority) && review.priority.length ? review.priority : ['先处理影响因果链和格式规范的高风险问题。'];
    const revisionPath = Array.isArray(review.revisionPath) && review.revisionPath.length ? review.revisionPath : [];
    const dimensions = Array.isArray(review.dimensions) && review.dimensions.length ? review.dimensions : [];
    const surgery = Array.isArray(review.surgeryTable) ? review.surgeryTable : [];
    const dimensionLabels = kind === 'storyboard' ? STORYBOARD_DIMENSION_LABELS : SCRIPT_DIMENSION_LABELS;
    const suggestionTitle = kind === 'storyboard' ? '修改建议' : '修改意见';
    const reviseTitle = kind === 'storyboard' ? '按审核意见重拆分' : '按意见修改';
    const statusLabel = review.status === 'passed' ? '审核通过' : '审核未通过';
    const summary = String(review.summary || review.overallVerdict || '分镜审核已返回结果');
    return `<div class="review-result">${kind === 'storyboard' ? '<div class="review-result-title">分镜审核结果</div>' : ''}<div class="review-top-grid"><section class="review-card review-score-card"><div class="review-card-title">综合评分</div><strong>${esc(review.score)}<small>/100</small></strong><b class="review-decision ${review.status === 'passed' ? 'pass' : 'revise'}">${statusLabel}</b><time>${esc(dateTime(review.at))}</time></section><section class="review-card review-dimensions-card"><div class="review-card-title">维度评分</div><div class="review-dimension-grid">${dimensions.map(item => { const key = String(item.key || item.name || ''); return `<div class="review-dimension-item"><div><strong>${esc(dimensionLabels[key] || key)}</strong><b>${esc(item.score)}</b></div><p>${esc(item.comment || '模型已完成该维度检查。')}</p></div>`; }).join('')}</div></section></div><section class="review-card review-summary-card"><div class="review-card-title">审核状态及描述</div><p>${esc(summary)}</p></section><section class="review-card"><div class="review-card-title">主要问题</div><ul class="review-list">${problems.map(item => `<li>${esc(item)}</li>`).join('')}</ul></section><section class="review-card"><div class="review-card-title">${suggestionTitle}</div><ul class="review-list">${suggestions.map(item => `<li>${esc(item)}</li>`).join('')}</ul></section><section class="review-card"><div class="review-card-title">执行优先级</div><ol class="review-list review-priority-list">${priorities.map(item => `<li>${esc(item)}</li>`).join('')}</ol></section>${revisionPath.length ? `<section class="review-card"><div class="review-card-title">修改路径</div><ol class="review-list review-priority-list">${revisionPath.map(item => `<li>${esc(item)}</li>`).join('')}</ol></section>` : ''}${surgery.length ? `<section class="review-card review-surgery-card"><div class="review-card-title">对白手术台</div>${surgery.map(item => `<div class="review-surgery-row"><div><span>原文</span><p>${esc(item.original || '—')}</p></div><div><span>诊断</span><p>${esc(item.diagnosis || '—')}</p></div><div><span>重写</span><p>${esc(item.rewrite || '—')}</p></div></div>`).join('')}</section>` : ''}${review.rewriteExample ? `<section class="review-card review-examples-card"><div class="review-card-title">优化方向示例</div><div class="review-example-body"><p>${esc(review.rewriteExample)}</p></div></section>` : ''}<div class="review-actions"><button class="btn primary" type="button" ${reviseAttr}>${reviseTitle}</button>${kind === 'script' ? '<button class="btn" type="button" data-confirm-next>确认，进入下一步</button>' : ''}</div></div>`;
  }
  function iconSvg(name, extraClass = '', size = 16) {
    const svgs = {
      hub: '<circle cx="12" cy="12" r="3" stroke-width="2"/><circle cx="19" cy="5" r="2.5" stroke-width="2"/><circle cx="5" cy="19" r="2.5" stroke-width="2"/><circle cx="19" cy="19" r="2.5" stroke-width="2"/><path d="M10 10.5L6.5 17.5M14 13.5L17.5 17.5M13.5 10.5L17.5 6.5" stroke-width="1.5"/>',
      memory: '<rect x="4" y="4" width="16" height="16" rx="2" stroke-width="2"/><rect x="9" y="9" width="6" height="6" stroke-width="1.5"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" stroke-width="2" stroke-linecap="round"/>',
      tune: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" stroke-width="2" stroke-linecap="round"/>',
      unfold_more: '<path d="M8 9l4-4 4 4M16 15l-4 4-4-4" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      theater_comedy: '<circle cx="12" cy="12" r="9" stroke-width="2"/><path d="M8 14.5c1 1.5 2.5 2 4 2s3-.5 4-2" stroke-width="1.5" stroke-linecap="round"/><circle cx="9" cy="10" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/>',
      videocam: '<rect x="2" y="6" width="13" height="12" rx="2" stroke-width="2"/><path d="M15 10l6-4v12l-6-4v-4z" stroke-width="1.5" stroke-linejoin="round"/>',
      timer: '<circle cx="12" cy="13" r="8" stroke-width="2"/><path d="M12 9v4l2.5 2" stroke-width="1.5" stroke-linecap="round"/><path d="M10 2h4" stroke-width="2" stroke-linecap="round"/>',
      play_arrow: '<polygon points="7 4 19 12 7 20 7 4" fill="currentColor"/>',
      replay: '<path d="M3.05 11a9 9 0 1 1 .5 4m-.5 5v-5h5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" stroke-width="2"/><polyline points="17 21 17 13 7 13 7 21" stroke-width="1.5"/><polyline points="7 3 7 8 15 8" stroke-width="1.5"/>',
      terminal: '<polyline points="4 17 10 11 4 5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="12" y1="19" x2="20" y2="19" stroke-width="2" stroke-linecap="round"/>',
      cyclone: '<circle cx="12" cy="12" r="9" stroke-width="2"/><path d="M12 3a9 9 0 0 0-7 14.5M12 21a9 9 0 0 0 7-14.5" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/>',
      history: '<circle cx="12" cy="12" r="9" stroke-width="2"/><polyline points="12 7 12 12 15 14" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      check_circle: '<circle cx="12" cy="12" r="9" stroke-width="2"/><path d="M8 12l2.5 2.5 5.5-5.5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      warning: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" stroke-width="2" stroke-linejoin="round"/><line x1="12" y1="9" x2="12" y2="13" stroke-width="2" stroke-linecap="round"/><line x1="12" y1="17" x2="12.01" y2="17" stroke-width="2" stroke-linecap="round"/>',
      flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="4" y1="22" x2="4" y2="15" stroke-width="2" stroke-linecap="round"/>',
      auto_fix_high: '<path d="M14.5 9.5l6-6a2.12 2.12 0 0 0-3-3l-6 6M18 16l2-2M12 22l-8.5-8.5a2.12 2.12 0 0 1 3-3L15 19l-3 3zM2 2l3 3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      arrow_back: '<line x1="19" y1="12" x2="5" y2="12" stroke-width="2" stroke-linecap="round"/><polyline points="12 19 5 12 12 5" stroke-width="2" stroke-linecap="round"/>',
      arrow_forward: '<line x1="5" y1="12" x2="19" y2="12" stroke-width="2" stroke-linecap="round"/><polyline points="12 5 19 12 12 19" stroke-width="2" stroke-linecap="round"/>',
      verified_user: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke-width="2" stroke-linejoin="round"/><polyline points="9 12 11 14 15 10" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      view_in_ar: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" stroke-width="1.8" stroke-linejoin="round"/><polyline points="3.27 6.96 12 12.01 20.73 6.96" stroke-width="1.8" stroke-linecap="round"/><line x1="12" y1="22.08" x2="12" y2="12" stroke-width="1.8"/>',
      apartment: '<rect x="4" y="2" width="16" height="20" rx="2" stroke-width="1.8"/><path d="M9 22v-4h6v4M8 6h.01M16 6h.01M8 10h.01M16 10h.01M8 14h.01M16 14h.01" stroke-width="2" stroke-linecap="round"/>',
      mouse: '<rect x="6" y="3" width="12" height="18" rx="6" stroke-width="1.8"/><line x1="12" y1="7" x2="12" y2="11" stroke-width="2" stroke-linecap="round"/>',
      category: '<rect x="3" y="3" width="7" height="7" rx="1" stroke-width="1.8"/><rect x="14" y="3" width="7" height="7" rx="1" stroke-width="1.8"/><rect x="14" y="14" width="7" height="7" rx="1" stroke-width="1.8"/><rect x="3" y="14" width="7" height="7" rx="1" stroke-width="1.8"/>',
      draw: '<path d="M12 19l7-7 3 3-7 7-3-3zM18 13l-1.5-1.5M2 22l4-1 12-12-3-3L3 18l-1 4z" stroke-width="1.8" stroke-linejoin="round"/>',
      refresh: '<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.19" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
      bolt: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="currentColor"/>',
      thermostat: '<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z" stroke-width="1.8"/>',
      playlist_play: '<line x1="4" y1="6" x2="16" y2="6" stroke-width="2" stroke-linecap="round"/><line x1="4" y1="12" x2="14" y2="12" stroke-width="2" stroke-linecap="round"/><line x1="4" y1="18" x2="10" y2="18" stroke-width="2" stroke-linecap="round"/><polygon points="16 13 22 16.5 16 20 16 13" fill="currentColor"/>',
      expand_more: '<polyline points="6 9 12 15 18 9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12" stroke-width="1.8"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" stroke-width="1.8"/>',
      account_circle: '<circle cx="12" cy="12" r="9" stroke-width="1.8"/><circle cx="12" cy="10" r="3" stroke-width="1.8"/><path d="M6.168 18.849a4 4 0 0 1 3.832-2.849h4a4 4 0 0 1 3.832 2.849" stroke-width="1.8"/>',
      movie_edit: '<path d="M4 4h10v16H4zM14 4l6 6M17 7l3 3M14 20l6-6" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
      view_timeline: '<rect x="3" y="4" width="18" height="16" rx="2" stroke-width="1.8"/><line x1="7" y1="8" x2="7" y2="12" stroke-width="2" stroke-linecap="round"/><line x1="12" y1="8" x2="12" y2="16" stroke-width="2" stroke-linecap="round"/><line x1="17" y1="12" x2="17" y2="16" stroke-width="2" stroke-linecap="round"/>',
      expand_all: '<polyline points="15 3 21 3 21 9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="9 21 3 21 3 15" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="21 15 21 21 15 21" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="3 9 3 3 9 3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      first_page: '<line x1="5" y1="4" x2="5" y2="20" stroke-width="2" stroke-linecap="round"/><polyline points="19 18 13 12 19 6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      last_page: '<line x1="19" y1="4" x2="19" y2="20" stroke-width="2" stroke-linecap="round"/><polyline points="5 6 11 12 5 18" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      auto_awesome: '<path d="M12 2l2.4 5.6L20 10l-5.6 2.4L12 18l-2.4-5.6L4 10l5.6-2.4L12 2zM19 16l1.2 2.8L23 20l-2.8 1.2L19 24l-1.2-2.8L15 20l2.8-1.2L19 16z" stroke-width="1.5" stroke-linejoin="round"/>',
      lock: '<rect x="3" y="11" width="18" height="11" rx="2" ry="2" stroke-width="1.8"/><path d="M7 11V7a5 5 0 0 1 10 0v4" stroke-width="1.8"/>',
      movie: '<rect x="2" y="4" width="20" height="16" rx="2" stroke-width="1.8"/><line x1="2" y1="9" x2="22" y2="9" stroke-width="1.8"/><line x1="7" y1="4" x2="5" y2="9" stroke-width="1.8"/><line x1="13" y1="4" x2="11" y2="9" stroke-width="1.8"/><line x1="19" y1="4" x2="17" y2="9" stroke-width="1.8"/>',
      graphic_eq: '<line x1="4" y1="10" x2="4" y2="14" stroke-width="2" stroke-linecap="round"/><line x1="8" y1="6" x2="8" y2="18" stroke-width="2" stroke-linecap="round"/><line x1="12" y1="3" x2="12" y2="21" stroke-width="2" stroke-linecap="round"/><line x1="16" y1="7" x2="16" y2="17" stroke-width="2" stroke-linecap="round"/><line x1="20" y1="11" x2="20" y2="13" stroke-width="2" stroke-linecap="round"/>',
      rate_review: '<path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z" stroke-width="1.8"/><path d="M12 11l4-4M8 15h2l5-5-2-2-5 5v2z" stroke-width="1.5"/>',
      chevron_left: '<polyline points="15 18 9 12 15 6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      chevron_right: '<polyline points="9 18 15 12 9 6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      history_edu: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" stroke-width="1.8"/>',
      precision_manufacturing: '<rect x="3" y="14" width="18" height="6" rx="1" stroke-width="1.8"/><path d="M6 14V8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v6M12 6Workbench" stroke-width="1.8"/>'
    };
    const body = svgs[name] || '<circle cx="12" cy="12" r="9" stroke-width="2"/>';
    return `<svg class="inline-block shrink-0 ${extraClass}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">${body}</svg>`;
  }
  function formatScreenplayHtml(text, pipeline) {
    if (!text || !text.trim()) {
      return `<div class="h-full flex flex-col items-center justify-center text-center p-8 text-neutral-500 gap-3 min-h-[260px]">
        ${iconSvg('description', 'w-10 h-10 text-gold-dim/40')}
        <div class="flex flex-col gap-1">
          <span class="font-display gw-type-body-md text-neutral-300 font-medium">暂无剧本正文</span>
          <span class="font-body gw-type-body-sm text-neutral-500">在左侧机架配置参数并点击「生成剧本」，或切换「编辑源码」模式直接撰写</span>
        </div>
      </div>`;
    }

    const lines = text.split('\n');
    let html = '';
    let actionBuffer = [];

    function flushAction() {
      if (actionBuffer.length) {
        html += `<p class="pl-2.5 border-l-2 border-gold-primary/60 text-neutral-200">${actionBuffer.join('<br/>')}</p>`;
        actionBuffer = [];
      }
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) {
        flushAction();
        continue;
      }
      if (line.startsWith('# ')) {
        flushAction();
        html += `<div class="flex items-center justify-between pb-2 border-b border-white/5">
          <h2 class="font-display gw-type-body-lg font-bold text-gold-light tracking-tight">${esc(line)}</h2>
          <span class="font-mono gw-type-body-sm text-neutral-500">DRAFT REV 1.4</span>
        </div>`;
      } else if (line.startsWith('> ')) {
        flushAction();
        html += `<div class="p-2 rounded bg-[#0b0d11] border border-white/5 font-mono gw-type-body-sm text-neutral-400 flex flex-col gap-1">
          <p><span class="text-gold-dim">&gt; 提示：</span>${esc(line.slice(2))}</p>
        </div>`;
      } else if (line.startsWith('## ')) {
        flushAction();
        html += `<div class="flex flex-col gap-2 pt-2 border-t border-white/5">
          <h3 class="font-mono gw-type-body-sm font-bold text-gold-primary">${esc(line)}</h3>
        </div>`;
      } else if (line.startsWith('**出场人物：') || line.startsWith('出场人物：')) {
        flushAction();
        html += `<p class="font-mono gw-type-body-sm text-neutral-500">${esc(line)}</p>`;
      } else if (line.startsWith('△') || line.startsWith('▲') || line.startsWith('- ')) {
        actionBuffer.push(esc(line));
      } else {
        flushAction();
        html += `<p class="text-neutral-300">${esc(line)}</p>`;
      }
    }
    flushAction();
    return html || `<p class="text-neutral-300 whitespace-pre-wrap">${esc(text)}</p>`;
  }

  function scriptMarkup(pipeline, stage, data) {
    const output = stageOutput(pipeline, stage);
    const hasGeneratedScript = Boolean(data.history.some(item => ['script-generated', 'script-revised'].includes(item.type)));
    const generatingScript = pendingFor(pipeline, 'scriptGeneration');
    const review = data.history.find(item => item.type === 'script-review') || null;
    const selectedMode = SCRIPT_MODES.find(item => item.key === data.scriptMode) || SCRIPT_MODES[0];
    const modeOptions = SCRIPT_MODES.map(item => `<option class="bg-[#101217] text-neutral-200" value="${esc(item.key)}"${item.key === selectedMode.key ? ' selected' : ''}>${esc(item.label)}</option>`).join('');

    const currentGenre = data.scriptGenre || '成长 / 剧情';
    const currentStyle = data.scriptStyle || '电影感写实';
    const currentDuration = data.scriptDuration || '1分钟 (01:00)';
    const currentPacing = SCRIPT_PACINGS.includes(data.scriptPacing) ? data.scriptPacing : '1.25x';

    const genreIdx = Math.max(0, SCRIPT_GENRES.indexOf(currentGenre));
    const styleIdx = Math.max(0, SCRIPT_STYLES.indexOf(currentStyle));
    const durationIdx = Math.max(0, SCRIPT_DURATIONS.indexOf(currentDuration));
    const pacingIdx = Math.max(0, SCRIPT_PACINGS.indexOf(currentPacing));

    const genreAngle = genreIdx * 60 - 90;
    const styleAngle = styleIdx * 60 - 45;
    const durationAngle = durationIdx * 72;

    const charCount = String(data.scriptDraft ? data.scriptDraft.length : 0).padStart(2, '0');
    const spec = STAGES[0];
    const modelKind = spec.modelKind || 'text';
    const models = modelOptions(modelKind);
    const selectedModel = selectedProviderModel(pipeline, 'script', modelKind);

    const isRunning = ['running', 'cancel_requested', 'recovering'].includes(stage.status);
    const isDone = stage.status === 'succeeded' || Boolean(output);
    const statusLedColor = isRunning ? 'bg-brand-orange shadow-[0_0_6px_#f97316] animate-pulse' : isDone ? 'bg-brand-green shadow-[0_0_6px_#22c55e]' : 'bg-neutral-500';

    const hasReview = Boolean(review && typeof review.score === 'number');
    const scoreVal = hasReview ? (Number(review.score) || 0) : '--';
    const isPass = hasReview ? (review.status === 'passed' || Number(review.score) >= 80) : false;
    const dashLength = hasReview ? (Number(review.score) / 100 * 251.3).toFixed(1) : 0;

    const defaultDims = [
      { name: '故事推进', score: '--', comment: '待送审评估' },
      { name: '角色与情感', score: '--', comment: '待送审评估' },
      { name: '对白质量', score: '--', comment: '待送审评估' },
      { name: '节奏控制', score: '--', comment: '待送审评估' },
      { name: '追读力', score: '--', comment: '待送审评估' },
      { name: '去 AI 味', score: '--', comment: '待送审评估' }
    ];

    const dimensions = hasReview && Array.isArray(review.dimensions) && review.dimensions.length ? review.dimensions : defaultDims;
    const problems = hasReview && Array.isArray(review.problems) && review.problems.length ? review.problems.map((p, idx) => {
      const parts = String(p).split('：');
      return {
        title: parts[0] || String(p),
        desc: parts[1] || '待核查处理。',
        red: idx < 2
      };
    }) : [];

    const dimCards = dimensions.map(dim => {
      const key = String(dim.key || dim.name || '');
      const label = SCRIPT_DIMENSION_LABELS[key] || key;
      const numScore = Number(dim.score);
      const isNum = Number.isFinite(numScore);
      const colorClass = isNum ? (numScore >= 80 ? 'text-gold-light' : numScore >= 70 ? 'text-gold-primary' : 'text-brand-red') : 'text-neutral-400';
      const barColor = isNum ? (numScore >= 80 ? 'bg-gold-light shadow-[0_0_6px_#fcdf9d]' : numScore >= 70 ? 'bg-gold-primary shadow-[0_0_6px_#dfc384]' : 'bg-brand-red shadow-[0_0_6px_#ef4444]') : 'bg-neutral-700';
      const barWidth = isNum ? Math.min(100, Math.max(5, numScore)) : 0;
      return `<div class="bay-inset p-3 rounded flex flex-col justify-between gap-1.5">
        <div class="flex items-center justify-between">
          <span class="font-display gw-type-body-sm text-neutral-300 font-medium">${esc(label)}</span>
          <span class="font-mono gw-type-body-md ${colorClass} font-bold">${esc(String(dim.score))}</span>
        </div>
        <div class="w-full h-1.5 bg-[#171a22] rounded-full overflow-hidden">
          <div class="h-full ${barColor}" style="width: ${barWidth}%;"></div>
        </div>
        <p class="font-body gw-type-body-sm text-neutral-400 line-clamp-2 leading-tight">${esc(dim.comment || '维度检验通过。')}</p>
      </div>`;
    }).join('');

    const problemCards = problems.length ? problems.map(prob => {
      return `<div class="flex items-start gap-2 bg-[#0c0d12] p-2 rounded border border-white/5">
        <span class="w-1.5 h-1.5 rounded-full ${prob.red ? 'bg-brand-red shadow-[0_0_6px_#ef4444]' : 'bg-brand-orange shadow-[0_0_6px_#f97316]'} mt-1.5 shrink-0"></span>
        <div class="flex flex-col">
          <span class="font-mono gw-type-body-sm text-neutral-200 font-semibold">${esc(prob.title)}</span>
          <span class="font-body gw-type-body-sm text-neutral-400">${esc(prob.desc)}</span>
        </div>
      </div>`;
    }).join('') : `<div class="col-span-2 p-3 text-center text-neutral-500 font-body gw-type-body-sm bg-[#0c0d12] rounded border border-white/5">暂无待处理问题。剧本生成或编辑后点击右侧「送审 (SUBMIT AUDIT)」，AI 审计员将在此输出问题排查与针对性修改清单。</div>`;

    return `<section class="stage stage-script ${statusClass(stage.status)} w-full flex flex-col gap-5" id="stage-script">
      <!-- Top Hardware Sub-Header Bay -->
      <section class="stitch-deck-top champagne-card rounded-lg">
        <div class="flex flex-col gap-1">
          <div class="flex items-center gap-2.5">
            <span class="w-2 h-2 rounded-full bg-gold-primary shadow-[0_0_8px_#dfc384]"></span>
            <span class="font-mono gw-type-body-smst text-gold-dim font-bold">STAGE PROTOCOL 01</span>
            <span class="text-neutral-600 font-mono">/</span>
            <span class="font-display gw-type-body-md text-gold-light font-semibold">剧本生成 (SCRIPT PLANNING)</span>
          </div>
          <div class="flex items-center gap-2 text-neutral-400 font-body gw-type-body-sm">
            ${iconSvg('hub', 'w-3.5 h-3.5 text-gold-primary')}
            <span>提示词链路：<strong class="text-neutral-200 font-medium">${esc(promptSource(pipeline, stage))}</strong></span>
            <span class="px-1.5 py-0.5 rounded bg-[#090a0d] font-mono gw-type-body-sm text-gold-dim border border-white/5">SYNAPSE OK</span>
          </div>
        </div>
        <!-- Model Selector Chiseled Socket -->
        <div class="bay-inset px-3 py-1.5 rounded flex items-center gap-3">
          <div class="flex flex-col">
            <span class="font-mono gw-type-body-sm text-neutral-500r">NEURAL ENGINE COUPLER</span>
            <div class="flex items-center gap-2">
              ${iconSvg('memory', 'w-4 h-4 text-gold-primary')}
              <select class="bg-transparent text-neutral-200 font-mono gw-type-body-md font-medium focus:outline-none cursor-pointer border-none p-0" data-stage-model="script" aria-label="选择剧本模型" ${models.length ? '' : 'disabled'}>
                ${models.length ? models.map(item => `<option class="bg-[#101217] text-neutral-200" value="${esc(`${item.provider_id}:::${item.model}`)}"${item.provider_id === selectedModel.provider_id && item.model === selectedModel.model ? ' selected' : ''}>${esc(item.provider_name)} / ${esc(item.label)}</option>`).join('') : '<option class="bg-[#101217]">未配置模型</option>'}
              </select>
            </div>
          </div>
          <div class="h-6 w-px bg-white/10"></div>
          <div class="flex items-center gap-1.5 bg-[#141720] px-2.5 py-1 rounded border border-gold-primary/30 shadow-[0_0_8px_rgba(223,195,132,0.15)]">
            <span class="w-1.5 h-1.5 rounded-full ${statusLedColor}"></span>
            <span class="font-mono gw-type-body-sm text-gold-light font-boldr">${esc(statusText(stage.status))}</span>
          </div>
        </div>
      </section>

      <!-- Main Dual-Bay Workstation Grid -->
      <div class="stitch-workstation-grid">
        <!-- Left Hardware Console: Input & Tactile Knobs (5 Cols) -->
        <section class="stitch-console-left champagne-card rounded-lg">
          <!-- Section Tag -->
          <div class="flex items-center justify-between pb-2 border-b border-white/5">
            <div class="flex items-center gap-2">
              ${iconSvg('tune', 'w-4 h-4 text-gold-primary')}
              <span class="font-display gw-type-body-md text-neutral-200 font-bold">剧本生成参数机架</span>
            </div>
            <span class="font-mono gw-type-body-sm text-gold-dimst">SLOT A-01</span>
          </div>

          <!-- Ingest Mode Selector -->
          <div class="flex flex-col gap-1.5">
            <label class="font-mono gw-type-body-md text-neutral-400r flex justify-between">
              <span>生成逻辑模式 (INGEST MODE)</span>
              <span class="text-gold-dim">${esc(selectedMode.key.toUpperCase())}</span>
            </label>
            <div class="relative">
              <select class="w-full appearance-none bg-[#090a0d] text-neutral-200 font-mono gw-type-body-md px-3 py-2 rounded border border-white/5 shadow-inner focus:outline-none focus:border-gold-primary/40 cursor-pointer" data-script-mode aria-label="选择生成逻辑模式">
                ${modeOptions}
              </select>
              ${iconSvg('unfold_more', 'w-4 h-4 absolute right-2.5 top-2.5 text-neutral-500 pointer-events-none')}
            </div>
          </div>

          <!-- Chiseled Text Input Bay -->
          <div class="flex flex-col gap-1.5">
            <label class="font-mono gw-type-body-md text-neutral-400r flex justify-between">
              <span>剧本母题与输入标头 (INGEST CAVITY)</span>
              <span class="font-mono text-neutral-500" id="scriptDraftCharCount">CHARS: ${charCount}/120</span>
            </label>
            <div class="bay-inset p-1.5 rounded">
              <textarea class="w-full bg-transparent text-neutral-200 font-body gw-type-body-md p-2 focus:outline-none placeholder:text-neutral-600 resize-none" data-script-draft placeholder="输入全集核心叙事核心..." rows="3">${esc(data.scriptDraft || pipeline.seed || '')}</textarea>
            </div>
          </div>

          <!-- Tactile Hardware Knobs & Precision Sliders (3 Knobs Array) -->
          <div class="bay-inset p-3.5 rounded flex flex-col gap-3.5">
            <div class="flex items-center justify-between">
              <span class="font-mono gw-type-body-sm text-gold-dimst">ANALOG ACTUATOR RACK</span>
              <span class="font-mono gw-type-body-sm text-brand-orange">CALIBRATION PASS</span>
            </div>
            <div class="knob-array-grid">
              <!-- Knob 1: Genre (题材预设) -->
              <div class="flex flex-col items-center gap-1.5">
                <span class="font-mono gw-type-body-sm text-neutral-400">题材预设</span>
                <div class="relative w-16 h-16 rounded-full bg-[#181a22] flex items-center justify-center shadow-[0_6px_16px_rgba(0,0,0,0.8),inset_0_1px_2px_rgba(255,255,255,0.15)] cursor-pointer group" data-knob="genre" role="button" tabindex="0" title="点击切换题材预设">
                  <svg class="absolute inset-0 w-full h-full pointer-events-none" style="transform: rotate(${genreAngle}deg);" viewBox="0 0 64 64">
                    <circle cx="32" cy="32" fill="none" r="26" stroke="#252934" stroke-dasharray="2 4" stroke-width="2.5"></circle>
                    <circle cx="32" cy="32" fill="none" r="26" stroke="#dfc384" stroke-dasharray="110 180" stroke-linecap="round" stroke-width="2.5"></circle>
                  </svg>
                  <div class="w-11 h-11 rounded-full bg-[#101217] shadow-[inset_0_2px_3px_rgba(255,255,255,0.1),0_3px_6px_rgba(0,0,0,0.9)] flex items-center justify-center relative">
                    <div class="absolute top-1 w-1 h-2 bg-gold-primary rounded-full shadow-[0_0_4px_#dfc384]"></div>
                    ${iconSvg('theater_comedy', 'w-3.5 h-3.5 text-gold-dim')}
                  </div>
                </div>
                <div class="bg-[#121419] px-2 py-0.5 rounded border border-white/5">
                  <span class="font-mono gw-type-body-sm text-gold-light font-semibold">${esc(currentGenre)}</span>
                </div>
                <input type="hidden" data-script-genre value="${esc(currentGenre)}">
              </div>

              <!-- Knob 2: Style (视听风格) -->
              <div class="flex flex-col items-center gap-1.5">
                <span class="font-mono gw-type-body-sm text-neutral-400">视听风格</span>
                <div class="relative w-16 h-16 rounded-full bg-[#181a22] flex items-center justify-center shadow-[0_6px_16px_rgba(0,0,0,0.8),inset_0_1px_2px_rgba(255,255,255,0.15)] cursor-pointer group" data-knob="style" role="button" tabindex="0" title="点击切换视听风格">
                  <svg class="absolute inset-0 w-full h-full pointer-events-none" style="transform: rotate(${styleAngle}deg);" viewBox="0 0 64 64">
                    <circle cx="32" cy="32" fill="none" r="26" stroke="#252934" stroke-dasharray="2 4" stroke-width="2.5"></circle>
                    <circle cx="32" cy="32" fill="none" r="26" stroke="#dfc384" stroke-dasharray="130 180" stroke-linecap="round" stroke-width="2.5"></circle>
                  </svg>
                  <div class="w-11 h-11 rounded-full bg-[#101217] shadow-[inset_0_2px_3px_rgba(255,255,255,0.1),0_3px_6px_rgba(0,0,0,0.9)] flex items-center justify-center relative">
                    <div class="absolute top-1 right-2 w-1 h-2 bg-gold-primary rounded-full shadow-[0_0_4px_#dfc384] rotate-45"></div>
                    ${iconSvg('videocam', 'w-3.5 h-3.5 text-gold-dim')}
                  </div>
                </div>
                <div class="bg-[#121419] px-2 py-0.5 rounded border border-white/5">
                  <span class="font-mono gw-type-body-sm text-gold-light font-semibold">${esc(currentStyle)}</span>
                </div>
                <input type="hidden" data-script-style value="${esc(currentStyle)}">
              </div>

              <!-- Knob 3: Duration (单集时长) -->
              <div class="flex flex-col items-center gap-1.5">
                <span class="font-mono gw-type-body-sm text-neutral-400">单集时长</span>
                <div class="relative w-16 h-16 rounded-full bg-[#181a22] flex items-center justify-center shadow-[0_6px_16px_rgba(0,0,0,0.8),inset_0_1px_2px_rgba(255,255,255,0.15)] cursor-pointer group" data-knob="duration" role="button" tabindex="0" title="点击切换单集时长">
                  <svg class="absolute inset-0 w-full h-full pointer-events-none" style="transform: rotate(${durationAngle}deg);" viewBox="0 0 64 64">
                    <circle cx="32" cy="32" fill="none" r="26" stroke="#252934" stroke-dasharray="2 4" stroke-width="2.5"></circle>
                    <circle cx="32" cy="32" fill="none" r="26" stroke="#dfc384" stroke-dasharray="60 180" stroke-linecap="round" stroke-width="2.5"></circle>
                  </svg>
                  <div class="w-11 h-11 rounded-full bg-[#101217] shadow-[inset_0_2px_3px_rgba(255,255,255,0.1),0_3px_6px_rgba(0,0,0,0.9)] flex items-center justify-center relative">
                    <div class="absolute left-1.5 top-4 w-2 h-1 bg-gold-primary rounded-full shadow-[0_0_4px_#dfc384]"></div>
                    ${iconSvg('timer', 'w-3.5 h-3.5 text-gold-dim')}
                  </div>
                </div>
                <div class="bg-[#121419] px-2 py-0.5 rounded border border-white/5">
                  <span class="font-mono gw-type-body-sm text-gold-light font-semibold">${esc(currentDuration)}</span>
                </div>
                <input type="hidden" data-script-duration value="${esc(currentDuration)}">
              </div>
            </div>

            <!-- Physical Linear Slider Track -->
            <div class="flex flex-col gap-1 pt-1">
              <div class="flex justify-between font-mono gw-type-body-sm text-neutral-400">
                <span>DENSITY BUS</span>
                <span data-script-pacing-label>PACING: ${esc(currentPacing)} SPEED</span>
              </div>
              <div class="h-2.5 w-full bg-[#090a0d] rounded-full p-0.5 border border-white/5 relative flex items-center">
                <div class="h-full bg-gradient-to-r from-gold-dim to-gold-primary rounded-full shadow-[0_0_6px_#dfc384]" style="width: ${Math.max(0, pacingIdx / (SCRIPT_PACINGS.length - 1) * 100)}%"></div>
                <div class="absolute -ml-1.5 w-3 h-5 bg-[#252933] rounded border border-gold-primary/50 shadow-[0_2px_4px_rgba(0,0,0,0.9)] flex items-center justify-center cursor-ew-resize" style="left: ${Math.max(0, pacingIdx / (SCRIPT_PACINGS.length - 1) * 100)}%">
                  <div class="w-0.5 h-2.5 bg-gold-primary"></div>
                </div>
                <input class="absolute inset-0 z-10 h-6 w-full cursor-ew-resize opacity-0" type="range" data-script-pacing min="0" max="${SCRIPT_PACINGS.length - 1}" step="1" value="${pacingIdx}" aria-label="节奏" title="调整节奏">
              </div>
            </div>
          </div>

          <!-- Generate and automatically save the resulting script version. -->
          <div class="flex items-center gap-3 pt-1">
            <button class="btn-stitch-primary flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded bg-gradient-to-b from-[#e5ca8f] to-[#bfa15d] text-[#12141a] font-mono gw-type-body-md font-boldr hover:brightness-110 shadow-[0_4px_12px_rgba(223,195,132,0.25),inset_0_1px_1px_rgba(255,255,255,0.4)] active:translate-y-px transition-all cursor-pointer" type="button" data-stage-${stage.status === 'queued' ? 'start' : 'retry'}="script" data-pipeline-id="${esc(pipeline.pipeline_id)}" ${generatingScript ? 'disabled aria-busy="true"' : ''}>
              ${iconSvg(hasGeneratedScript ? 'replay' : 'play_arrow', 'w-4 h-4')}
              <span>${generatingScript ? '生成中…' : hasGeneratedScript ? '重新生成' : '生成剧本'}</span>
            </button>
          </div>

          <p class="font-mono gw-type-body-sm text-neutral-500 leading-tight">
            当前模式调用: ${esc(promptDocument(pipeline, selectedMode.prompt).name)}。生成请求直接发送至当前选中文本模型，结果会自动回写到右侧视窗并归档为剧本资产。
          </p>
        </section>

        <!-- Right Screenplay Terminal Viewport (7 Cols) -->
        <section class="stitch-viewport-right champagne-card rounded-lg p-4 flex flex-col justify-between gap-3 relative">
          <!-- Top Bezel & Indicator Bay -->
          <div class="flex items-center justify-between pb-2 border-b border-white/5">
            <div class="flex items-center gap-2.5">
              <div class="flex items-center gap-1.5 bg-[#090a0d] px-2 py-0.5 rounded border border-white/5">
                <span class="w-1.5 h-1.5 rounded-full bg-gold-primary shadow-[0_0_6px_#dfc384]"></span>
                <span class="font-mono gw-type-body-sm text-gold-primary font-boldst">LIVE CRT BUFFER</span>
              </div>
              <span class="font-display gw-type-body-md text-neutral-200 font-semibold">生成结果视窗 (${output ? '已回写，可继续编辑' : '完成生成后显示'})</span>
            </div>
            <div class="flex items-center gap-2.5">
              <button type="button" class="gw-type-body-md font-mono px-2 py-0.5 rounded bg-[#101218] hover:bg-[#1a1d26] text-gold-dim border border-white/10 cursor-pointer" data-toggle-script-editor title="在富文本排版与纯文本源码编辑之间切换">
                ${state.scriptEditorMode === 'edit' ? '预览排版' : '编辑源码'}
              </button>
              <span class="font-mono gw-type-body-sm text-gold-dim">BUFFER ID: SCR-${esc(pipeline.pipeline_id.slice(-5).toUpperCase())}</span>
              <div class="flex items-center gap-1.5 bg-[#090a0d] px-2 py-0.5 rounded border border-white/5">
                <span class="w-1.5 h-1.5 rounded-full ${output ? 'bg-brand-green' : 'bg-neutral-600'}"></span>
                <span class="font-mono gw-type-body-sm text-neutral-300 font-bold">${output ? '已保存' : '待就绪'}</span>
              </div>
            </div>
          </div>

          <!-- Screenplay Document Scrollport -->
          <div class="bay-inset p-3.5 rounded h-[340px] overflow-y-auto flex flex-col gap-3 font-body gw-type-body-sm leading-relaxed select-text">
            ${state.scriptEditorMode === 'edit' ? `<textarea class="w-full h-full bg-transparent text-neutral-200 font-mono gw-type-body-sm leading-relaxed p-1 focus:outline-none placeholder:text-neutral-600 resize-none select-text custom-scrollbar" data-script-output placeholder="剧本正文会显示在这里，生成完成后支持在此直接编辑与随时保存...">${esc(output)}</textarea>` : formatScreenplayHtml(output, pipeline)}
          </div>

          <!-- Action Footer: Checksum & High-End Submit Audit Ignition Button -->
          <div class="flex items-center justify-between pt-2 border-t border-white/5">
            <div class="flex items-center gap-2">
              ${iconSvg('terminal', 'w-3.5 h-3.5 text-neutral-500')}
              <span class="font-mono gw-type-body-sm text-neutral-500">CHECKSUM: 0x${checksum(output || pipeline.pipeline_id)}</span>
            </div>
            <!-- Submit Audit Button -->
            <button class="btn-stitch-ignite flex items-center gap-2.5 px-5 py-2 rounded bg-gradient-to-r from-[#dfc384] to-[#f4e1b9] text-[#121417] font-display gw-type-body-md font-boldr hover:brightness-110 shadow-[0_6px_18px_rgba(223,195,132,0.35),inset_0_1px_2px_rgba(255,255,255,0.6)] active:scale-95 transition-all cursor-pointer" type="button" data-script-review>
              ${iconSvg('cyclone', 'w-[18px] h-[18px]', 18)}
              <span>送审 (SUBMIT AUDIT)</span>
            </button>
          </div>
        </section>
      </div>

      <!-- ===================================================================== -->
      <!-- BOTTOM HARDWARE INSPECTION RACK (AUDIT AGENT SECTION)                 -->
      <!-- ===================================================================== -->
      <section class="champagne-card rounded-lg p-4 flex flex-col gap-4">
        <!-- Header of Inspection Module -->
        <div class="flex items-center justify-between pb-2 border-b border-white/5">
          <div class="flex items-center gap-2.5">
            <div class="w-2 h-2 rounded-full ${isPass ? 'bg-brand-green shadow-[0_0_8px_#22c55e]' : 'bg-brand-red shadow-[0_0_8px_#ef4444]'}"></div>
            <span class="font-display gw-type-body-md text-neutral-200 font-bold">审核结果机架面板</span>
            <span class="font-mono gw-type-body-sm text-gold-dim">[AUDIT AGENT 审计总线]</span>
          </div>
          <div class="flex items-center gap-2 bg-[#090a0d] px-2.5 py-1 rounded border border-white/5">
            <span class="font-mono gw-type-body-sm text-neutral-500">PROTOCOL</span>
            <span class="font-mono gw-type-body-sm text-gold-primary">AUDIT_CRITIQUE_MK4</span>
          </div>
        </div>
        <!-- Telemetry Master Grid -->
        <div class="stitch-audit-grid">
          <!-- Left Dial: Composite Score Gauge (3 Cols) -->
          <div class="bay-inset p-3.5 rounded flex flex-col items-center justify-center relative">
            <span class="font-mono gw-type-body-sm text-neutral-400st mb-2">综合质量评分 (OVERALL)</span>
            <!-- Amber Gold Circular Dial -->
            <div class="relative w-32 h-32 flex items-center justify-center">
              <svg class="w-full h-full -rotate-90 pointer-events-none" viewBox="0 0 100 100">
                <circle cx="50" cy="50" fill="none" r="40" stroke="#232733" stroke-dasharray="195 260" stroke-linecap="round" stroke-width="7"></circle>
                <circle class="drop-shadow-[0_0_8px_rgba(223,195,132,0.4)]" cx="50" cy="50" fill="none" r="40" stroke="${hasReview ? (isPass ? '#22c55e' : '#dfc384') : '#333742'}" stroke-dasharray="${dashLength} 260" stroke-linecap="round" stroke-width="7"></circle>
              </svg>
              <div class="absolute flex flex-col items-center">
                <span class="font-display gw-type-headline-lg font-bold text-gold-lighter leading-none">${scoreVal}</span>
                <span class="font-mono gw-type-body-sm text-neutral-500 mt-0.5">/ 100 PTS</span>
              </div>
            </div>
            <!-- Warning Plaque Badge -->
            <div class="mt-2.5 flex flex-col items-center gap-0.5 ${hasReview ? (isPass ? 'bg-brand-green/10 border border-brand-green/30' : 'bg-brand-red/10 border border-brand-red/30') : 'bg-neutral-800/40 border border-white/10'} px-3 py-1 rounded">
              <div class="flex items-center gap-1.5 ${hasReview ? (isPass ? 'text-brand-green' : 'text-brand-red') : 'text-neutral-400'}">
                ${iconSvg(hasReview ? (isPass ? 'check_circle' : 'warning') : 'schedule', 'w-3.5 h-3.5')}
                <span class="font-mono gw-type-body-md font-boldr">${hasReview ? (isPass ? '审核通过' : '审核未通过') : '待送审'}</span>
              </div>
              <span class="font-mono gw-type-body-sm text-neutral-400">${esc(review?.at ? dateTime(review.at) : '等待发起送审')}</span>
            </div>
          </div>
          <!-- Right Multi-Channel Gauge Array: 6 Dimensions (9 Cols) -->
          <div class="stitch-dimensions-grid">
            ${dimCards}
          </div>
        </div>
        <!-- 4 Critical Revision Checklist Cards -->
        <div class="bay-inset p-3 rounded flex flex-col gap-2">
          <div class="flex items-center justify-between pb-1.5 border-b border-white/5">
            <span class="font-mono gw-type-body-sm text-brand-redr font-bold flex items-center gap-1.5">
              ${iconSvg('flag', 'w-3.5 h-3.5')}
              <span>审计核心问题排查清单 (CRITICAL ISSUES DETECTED)</span>
            </span>
            <span class="font-mono gw-type-body-sm text-neutral-500">${problems.length} ENTRIES PENDING REVISION</span>
          </div>
          <div class="stitch-problems-grid">
            ${problemCards}
          </div>
        </div>

        <!-- Bottom Action Bar for Revision & Next -->
        <div class="flex items-center justify-end gap-3 pt-2 border-t border-white/5">
          <button class="btn-stitch-secondary flex items-center gap-2 py-2 px-4 rounded bg-[#181b24] hover:bg-[#202430] text-neutral-200 font-mono gw-type-body-mdr border border-white/5 shadow-[0_4px_10px_rgba(0,0,0,0.6)] cursor-pointer" type="button" data-revise-script>
            ${iconSvg('auto_fix_high', 'w-4 h-4 text-gold-primary')}
            <span>按意见修改</span>
          </button>
          <button class="btn-stitch-primary flex items-center gap-2 py-2 px-4 rounded bg-gradient-to-b from-[#e5ca8f] to-[#bfa15d] text-[#12141a] font-mono gw-type-body-md font-boldr hover:brightness-110 shadow-[0_4px_12px_rgba(223,195,132,0.25)] cursor-pointer" type="button" data-confirm-next>
            <span>确认，进入下一步</span>
            ${iconSvg('arrow_forward', 'w-4 h-4')}
          </button>
        </div>
      </section>

      <!-- History Versions Strip -->
      <div class="stitch-history-bar">
        <div class="stitch-history-left">
          ${iconSvg('history', 'w-3.5 h-3.5 text-gold-dim')}
          <span class="text-gold-dim font-bold">历史版本:</span>
          <div class="flex items-center gap-1.5 flex-wrap">
            ${data.history.filter(item => item.type === 'script-review' || item.type === 'script-generated' || item.type === 'script-revised').slice(0, 8).map(item => `<button type="button" class="px-2 py-0.5 rounded bg-[#161820] hover:bg-[#222633] text-neutral-300 border border-white/5 cursor-pointer" data-history-item="${esc(item.at)}">${esc(item.type === 'script-review' ? `审核 ${item.score}分` : item.type === 'script-revised' ? '按意见修改' : '生成版本')} · ${esc(dateTime(item.at))}</button>`).join('') || '<span class="text-neutral-600">首次生成后将保留历史版本</span>'}
          </div>
        </div>
        <span class="text-neutral-600 gw-type-body-sm">ROLLBACK READY</span>
      </div>

      <!-- Footer Strip -->
      <footer class="w-full mt-6 pt-3 pb-1 border-t border-white/5 flex items-center justify-between text-neutral-500 font-mono gw-type-body-sm">
        <div class="flex items-center gap-3">
          <span class="text-gold-dim">GODS' WORKBENCH ARCHITECTURE</span>
          <span>SYS-REV 4.8.2-NEO</span>
        </div>
        <div class="flex items-center gap-4">
          <span>CLOCK: ATOMIC LOCK 10MHz</span>
          <span>© 2026 ALL RIGHTS RESERVED</span>
        </div>
      </footer>
    </section>`;
  }

  function assetGroupFilter(pipeline, data) {
    const groups = [
      { key: 'all', label: '全部', count: data.assets.length },
      { key: 'character', label: '角色', count: data.assets.filter(item => item.type === 'character').length },
      { key: 'scene', label: '场景', count: data.assets.filter(item => item.type === 'scene').length },
      { key: 'prop', label: '道具', count: data.assets.filter(item => item.type === 'prop').length },
      { key: 'other', label: '其他', count: data.assets.filter(item => item.type === 'other').length }
    ];
    const selected = state.assetGroups[pipeline.pipeline_id] || 'all';

    return `<div class="flex items-center p-1 rounded-xl bg-[#06070a] border border-[#1c1f2b] shadow-[inset_0_2px_5px_rgba(0,0,0,0.9)] gap-1 flex-wrap" role="tablist" aria-label="资产分类筛选">
      ${groups.map(grp => {
        const isActive = selected === grp.key;
        if (isActive) {
          return `<button class="relative px-3.5 py-1.5 rounded-lg bg-[#181c26] text-primary font-mono gw-type-body-md font-bold shadow-[0_2px_8px_rgba(0,0,0,0.6),inset_0_1px_1px_rgba(255,255,255,0.15)] flex items-center gap-2 border border-primary/30 cursor-pointer" type="button" role="tab" aria-selected="true" data-asset-group="${esc(grp.key)}" data-pipeline-id="${esc(pipeline.pipeline_id)}">
            <span>${esc(grp.label)}</span>
            <span class="px-1.5 py-0.5 rounded-full bg-primary text-[#1a1308] gw-type-body-md font-bold">${grp.count}</span>
            <span class="absolute -bottom-1 left-1/2 -translate-x-1/2 w-8 h-[2px] bg-primary rounded-full blur-[1px]"></span>
          </button>`;
        } else {
          return `<button class="px-3.5 py-1.5 rounded-lg text-[#888173] hover:text-[#d3cbbe] font-mono gw-type-body-md transition-all flex items-center gap-2 cursor-pointer hover:bg-white/5" type="button" role="tab" aria-selected="false" data-asset-group="${esc(grp.key)}" data-pipeline-id="${esc(pipeline.pipeline_id)}">
            <span>${esc(grp.label)}</span>
            <span class="px-1.5 py-0.5 rounded-full bg-[#101218] text-[#71695b] gw-type-body-sm">${grp.count}</span>
          </button>`;
        }
      }).join('')}
    </div>`;
  }

  function assetCard(asset, pipeline, index) {
    const pipelineId = esc(pipeline.pipeline_id);
    const assetId = esc(asset.id);
    const idFormatted = String((index !== undefined ? index : 0) + 1).padStart(2, '0');
    const isReady = asset.status === 'ready' || Boolean(asset.preview);
    const isRunning = asset.status === 'running';

    let statusBadge = '';
    if (isRunning) {
      statusBadge = `<div class="flex items-center gap-1.5 bg-[#050609] px-2.5 py-1 rounded border border-[#1b1f2b] shadow-[inset_0_1px_2px_rgba(0,0,0,0.8)]">
        <span class="w-2 h-2 rounded-full bg-primary animate-ping"></span>
        <span class="font-mono gw-type-body-sm text-primary font-boldr">● 生成中</span>
      </div>`;
    } else if (isReady) {
      statusBadge = `<div class="flex items-center gap-1.5 bg-[#050609] px-2.5 py-1 rounded border border-[#1b1f2b] shadow-[inset_0_1px_2px_rgba(0,0,0,0.8)]">
        <span class="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_6px_#34d399]"></span>
        <span class="font-mono gw-type-body-sm text-emerald-400 font-boldr">已就绪</span>
      </div>`;
    } else {
      statusBadge = `<div class="flex items-center gap-1.5 bg-[#050609] px-2.5 py-1 rounded border border-[#1b1f2b] shadow-[inset_0_1px_2px_rgba(0,0,0,0.8)]">
        <span class="w-2 h-2 rounded-full bg-[#625c50]"></span>
        <span class="font-mono gw-type-body-sm text-[#8a8274] font-boldr">待生成</span>
      </div>`;
    }

    const typeLabel = assetTypeLabel(asset.type);
    let typeIcon = 'view_in_ar';
    let typeColor = 'text-primary';
    if (asset.type === 'scene') {
      typeIcon = 'apartment';
      typeColor = 'text-[#ffb689]';
    } else if (asset.type === 'prop') {
      typeIcon = 'mouse';
      typeColor = 'text-[#c3e7ff]';
    } else if (asset.type === 'character') {
      typeIcon = 'view_in_ar';
      typeColor = 'text-primary';
    } else {
      typeIcon = 'category';
      typeColor = 'text-[#d7c59f]';
    }

    let viewPort = '';
    if (asset.preview) {
      viewPort = `<div class="relative w-full h-[220px] rounded-lg bg-[#050608] border border-[#1e2330] overflow-hidden shadow-[inset_0_3px_8px_rgba(0,0,0,0.95)] cursor-pointer group-hover:border-primary/40 transition-colors" data-asset-image data-pipeline-id="${pipelineId}" data-asset-id="${assetId}">
        <img class="w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-500" src="${esc(asset.preview)}" alt="${esc(asset.name)}" loading="lazy" title="双击查看大图">
        <div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-[#06070a] via-[#06070a]/80 to-transparent p-2 flex items-center justify-between pointer-events-none">
          <span class="font-mono gw-type-body-sm text-primary font-bold">RENDER TILE: 09/09</span>
          <span class="font-mono gw-type-body-sm text-[#938b7d]">LATENT READY</span>
        </div>
      </div>`;
    } else if (isRunning) {
      viewPort = `<div class="w-full h-[220px] rounded-lg bg-[#050608] border border-[#1e2330] overflow-hidden shadow-[inset_0_3px_8px_rgba(0,0,0,0.95)] flex flex-col items-center justify-center gap-2">
        <div class="w-10 h-10 rounded-full border-2 border-primary/30 border-t-primary animate-spin"></div>
        <span class="font-mono gw-type-body-sm text-primaryst font-bold">渲染采样中...</span>
        <span class="font-mono gw-type-body-sm text-[#716a5e]">LATENT SAMPLING 32/40</span>
      </div>`;
    } else {
      viewPort = `<div class="w-full h-[220px] rounded-lg bg-[#050608] border border-[#1c202c] shadow-[inset_0_3px_8px_rgba(0,0,0,0.95)] flex flex-col items-center justify-center gap-2 text-[#797365] group-hover:${typeColor} transition-colors cursor-pointer" data-asset-preview data-pipeline-id="${pipelineId}" data-asset-id="${assetId}">
        <div class="w-12 h-12 rounded-full bg-[#12151e] border border-[#232837] flex items-center justify-center shadow-[inset_0_1px_2px_rgba(255,255,255,0.08),0_4px_8px_rgba(0,0,0,0.7)] group-hover:border-primary/40 group-hover:scale-105 transition-all">
          ${iconSvg(typeIcon, 'w-6 h-6 text-current', 24)}
        </div>
        <span class="font-mono gw-type-body-smst font-bold">待生成图片</span>
        <span class="font-mono gw-type-body-sm text-[#554f44]">双击卡片查看设定</span>
      </div>`;
    }

    let genBtnClass = 'bg-gradient-to-b from-[#fcdf9d] to-[#dfc384] text-[#1a1308] shadow-[0_3px_12px_rgba(223,195,132,0.35),inset_0_1px_1px_rgba(255,255,255,0.6)] hover:brightness-105';
    let genBtnInner = `${iconSvg('draw', 'w-3.5 h-3.5 shrink-0')}<span>${isReady ? '重新生成' : (asset.status === 'running' ? '生成中' : '生成资产')}</span>`;
    if (isRunning) {
      genBtnClass = 'bg-gradient-to-b from-[#fcdf9d] to-[#dfc384] text-[#1a1308] opacity-80 cursor-wait';
      genBtnInner = `${iconSvg('refresh', 'w-3.5 h-3.5 shrink-0 animate-spin')}<span>${asset.status === 'running' ? '生成中' : '生成资产'}...</span>`;
    }

    return `<div class="flex-none w-[340px] bg-[#0c0e14] rounded-xl border border-[#222736] p-4 shadow-[0_14px_30px_rgba(0,0,0,0.85),inset_0_1px_1px_rgba(255,255,255,0.08)] flex flex-col justify-between gap-4 group relative asset-rack-card" tabindex="0" data-asset-card data-pipeline-id="${pipelineId}" data-asset-id="${assetId}" aria-label="${esc(asset.name)}，${esc(typeLabel)}">
      <span class="milled-screw absolute top-2.5 left-2.5"></span>
      <span class="milled-screw absolute top-2.5 right-2.5"></span>

      <div class="flex flex-col gap-3">
        <div class="flex items-center justify-between">
          ${statusBadge}
          <span class="font-mono gw-type-body-sm text-[#736c5f]">ASSET_ID #${idFormatted}</span>
        </div>

        ${viewPort}

        <div class="flex flex-col gap-1">
          <div class="flex items-center justify-between">
            <h3 class="font-display gw-type-body-lg font-bold text-[#f5ebd7] truncate" title="${esc(asset.name)}">${esc(asset.name)}</h3>
            <span class="px-2 py-0.5 rounded bg-[#151822] border border-[#282d3c] font-mono gw-type-body-sm ${typeColor} font-bold">${esc(typeLabel)}</span>
          </div>
          <p class="gw-type-body-sm text-[#9e9686] line-clamp-2 leading-relaxed" title="${esc(asset.prompt)}">${esc(asset.prompt)}</p>
        </div>
      </div>

      <div class="flex items-center gap-2 pt-2 border-t border-[#191c26]">
        <button class="asset-generate-button flex-1 py-2 rounded-lg ${genBtnClass} font-mono gw-type-body-md font-bold active:translate-y-px transition-all flex items-center justify-center gap-1.5 cursor-pointer" type="button" data-asset-generate="${assetId}" data-pipeline-id="${pipelineId}" ${isRunning ? 'disabled' : ''}>
          ${genBtnInner}
        </button>
        <button class="px-3 py-2 rounded-lg bg-[#141720] hover:bg-[#1c202d] text-[#b3aa9a] hover:text-[#f0eae1] border border-[#272d3e] shadow-[0_2px_6px_rgba(0,0,0,0.6)] active:translate-y-px transition-all font-mono gw-type-body-md font-medium cursor-pointer" type="button" data-asset-upload="${assetId}" data-pipeline-id="${pipelineId}">
          上传本地
        </button>
      </div>
    </div>`;
  }

  function bindAssetTrackSync() {
    requestAnimationFrame(() => {
      const slider = document.getElementById('cardsSliderRack');
      const track = document.getElementById('hardwareTrack');
      const filament = document.getElementById('trackFilament');
      const thumb = document.getElementById('trackThumb');
      if (!slider || !track || !filament || !thumb) return;

      const updateSliderThumb = () => {
        const maxScroll = slider.scrollWidth - slider.clientWidth;
        if (maxScroll <= 0) {
          filament.style.width = '100%';
          thumb.style.left = '50%';
          return;
        }
        const progress = slider.scrollLeft / maxScroll;
        const percentage = Math.min(Math.max(progress * 100, 4), 96);
        filament.style.width = percentage + '%';
        thumb.style.left = percentage + '%';
      };

      slider.removeEventListener('scroll', slider._syncFn || (()=>{}));
      slider._syncFn = updateSliderThumb;
      slider.addEventListener('scroll', updateSliderThumb, { passive: true });

      track.onclick = (e) => {
        const rect = track.getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        const progress = Math.min(Math.max(clickX / rect.width, 0), 1);
        const maxScroll = slider.scrollWidth - slider.clientWidth;
        slider.scrollTo({ left: maxScroll * progress, behavior: 'smooth' });
      };

      updateSliderThumb();
    });
  }

  async function batchGenerateAssets(pipeline) {
    const data = localData(pipeline);
    const pendingList = data.assets.filter(item => item.status !== 'ready' && !item.preview);
    if (!pendingList.length) {
      toast('所有资产图片均已生成完毕');
      return;
    }
    const selection = selectedProviderModel(pipeline, 'asset_image', 'image');
    if (!selection.provider_id || !selection.model) {
      toast('请先在右上方选择图片模型', true);
      return;
    }
    toast(`正在批量生成 ${pendingList.length} 项资产图片...`);
    for (const item of pendingList) {
      try {
        await assetGenerate(pipeline, item.id);
      } catch (err) {
        console.warn('批量生图遇到异常:', item.name, err);
      }
    }
  }

  function assetsMarkup(pipeline, stage, data) {
    const selectedGroup = state.assetGroups[pipeline.pipeline_id] || 'all';
    const visibleAssets = selectedGroup === 'all' ? data.assets : data.assets.filter(item => item.type === selectedGroup);
    const extracting = ['running', 'cancel_requested', 'recovering'].includes(stage.status);

    const textModel = selectedProviderModel(pipeline, 'assets', 'text');
    // 无真实 providers 配置时**不得**回退到具体厂商/模型名（会让人误以为已选中某模型）。
    const textModelDisplay = textModel.provider_name ? `${textModel.provider_name} / ${textModel.label || textModel.model}` : '未配置模型';

    const imgModels = modelOptions('image');
    const selectedImgModel = selectedProviderModel(pipeline, 'asset_image', 'image');
    const imgModelDisplay = selectedImgModel.provider_name ? `${selectedImgModel.provider_name} / ${selectedImgModel.label || selectedImgModel.model}` : (imgModels[0] ? `${imgModels[0].provider_name} / ${imgModels[0].label}` : '未配置模型');
    const imgModelOptions = imgModels.length ? imgModels.map(item => `<option value="${esc(`${item.provider_id}:::${item.model}`)}"${item.provider_id === selectedImgModel.provider_id && item.model === selectedImgModel.model ? ' selected' : ''}>${esc(item.provider_name)} / ${esc(item.label)}</option>`).join('') : '<option value="">未配置模型</option>';

    const totalCount = data.assets.length;
    const pendingCount = data.assets.filter(item => item.status !== 'ready' && !item.preview).length;

    const promptDoc = promptDocument(pipeline, stagePromptItem(pipeline, stage));
    const promptLink = `提示词链路：${state.libraries.find(item => item.id === (pipeline?.prompt_library_id || 'episode'))?.name || '当前项目剧集提示词库'} · ${promptDoc.name || 'Character Asset Extraction'}`;

    return `<section class="stage stage-assets ${statusClass(stage.status)} w-full flex flex-col gap-5" id="stage-assets">
      <!-- TOP CONSOLE: 模型与管线配置条 (Chassis Upper Deck) -->
      <section class="w-full bg-[#0b0d12] rounded-xl border border-[#1f232e] p-4 shadow-[0_12px_28px_rgba(0,0,0,0.8),inset_0_1px_1px_rgba(255,255,255,0.06)] flex flex-col gap-4 relative">
        <span class="milled-screw absolute top-2.5 left-2.5"></span>
        <span class="milled-screw absolute top-2.5 right-2.5"></span>

        <div class="flex flex-wrap items-center justify-between gap-4 pb-3 border-b border-[#181b24]">
          <div class="flex flex-col gap-1">
            <div class="flex items-center gap-2">
              <span class="px-2 py-0.5 rounded bg-[#171a23] text-primary font-mono gw-type-body-md font-boldr border border-primary/20">STEP MODULE 02</span>
              <span class="font-mono gw-type-body-sm text-[#7c7567]">NODE PIPELINE: ASSET_EXTRACT_V4</span>
            </div>
            <div class="flex items-baseline gap-3">
              <h1 class="font-display gw-type-headline-md font-bold text-[#f2ede4]">资产生成</h1>
              <span class="gw-type-body-sm text-primary/90 font-normal">${esc(promptLink)}</span>
            </div>
          </div>

          <div class="flex items-center gap-3">
            <div class="flex items-center gap-1.5 bg-[#06070a] p-1 rounded-lg border border-[#181b24] shadow-[inset_0_2px_4px_rgba(0,0,0,0.85)]">
              <button class="px-2 py-0.5 rounded gw-type-body-md font-mono text-[#8a8274] hover:text-[#f2ede4] hover:bg-[#141722] transition-colors flex items-center gap-1 cursor-pointer" type="button" data-goto-step="script" title="返回上一步：01 剧本生成">
                ${iconSvg('arrow_back', 'w-3 h-3')}
                <span>01 剧本</span>
              </button>
              <span class="text-[#2e3444] gw-type-body-sm">/</span>
              <span class="px-2 py-0.5 rounded bg-[#161923] gw-type-body-sm font-mono font-bold text-primary border border-primary/25 shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)]">02 资产</span>
              <span class="text-[#2e3444] gw-type-body-sm">/</span>
              <button class="px-2 py-0.5 rounded gw-type-body-md font-mono text-[#8a8274] hover:text-[#f2ede4] hover:bg-[#141722] transition-colors flex items-center gap-1 cursor-pointer" type="button" data-goto-step="video" title="进入下一步：03 分镜脚本">
                <span>03 分镜</span>
                ${iconSvg('arrow_forward', 'w-3 h-3')}
              </button>
            </div>

            <div class="flex items-center gap-2.5 bg-[#06070a] px-3 py-1.5 rounded-lg border border-[#181b24] shadow-[inset_0_2px_4px_rgba(0,0,0,0.9)]">
              <div class="flex flex-col text-right">
                <span class="font-mono gw-type-body-sm text-[#716a5e]">COMPUTE BUS</span>
                <span class="font-mono gw-type-body-sm text-amber-300 font-bold" data-gw-degradation="not_integrated" title="未接入硬件遥测端点，无真实算力数据">未接入</span>
              </div>
              <div class="w-1.5 h-6 bg-[#13161f] rounded-full overflow-hidden flex flex-col justify-end p-0.5">
                <div class="w-full h-4/5 bg-[#ffb689] rounded-full shadow-[0_0_6px_#ffb689]"></div>
              </div>
            </div>
            <div class="flex items-center gap-2.5 bg-[#06070a] px-3 py-1.5 rounded-lg border border-[#181b24] shadow-[inset_0_2px_4px_rgba(0,0,0,0.9)]">
              <div class="flex flex-col text-right">
                <span class="font-mono gw-type-body-sm text-[#716a5e]">VRAM CACHE</span>
                <span class="font-mono gw-type-body-sm text-amber-300 font-bold" data-gw-degradation="not_integrated" title="未接入显存遥测端点，无真实显存占用数据">未接入</span>
              </div>
              <span class="w-2 h-2 rounded-full bg-primary animate-pulse shadow-[0_0_8px_#dfc384]"></span>
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-12 gap-3.5 items-center">
          <div class="lg:col-span-5 bg-[#07080c] p-2.5 rounded-lg border border-[#191c26] shadow-[inset_0_2px_5px_rgba(0,0,0,0.85)] flex flex-col gap-1.5">
            <div class="flex items-center justify-between">
              <span class="font-mono gw-type-body-sm text-[#857d6d]r">文本模型 (EXTRACTION ENGINE)</span>
              <span class="flex items-center gap-1 font-mono gw-type-body-sm text-amber-300" data-gw-degradation="not_integrated" title="未连接任何模型服务，未验证任何模型可用性">
                <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse shadow-[0_0_5px_#f59e0b]"></span>未接入
              </span>
            </div>
            <div class="flex items-center justify-between bg-[#11131a] px-3 py-2 rounded border border-[#232734]">
              <div class="flex items-center gap-2 truncate">
                ${iconSvg('terminal', 'w-4 h-4 text-[#7a8194] shrink-0')}
                <span class="font-mono gw-type-body-sm font-semibold text-[#f0eae1] truncate">${esc(textModelDisplay)}</span>
              </div>
              <span class="font-mono gw-type-body-sm px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30 font-bold shrink-0" data-gw-degradation="not_integrated" title="未连接任何模型服务，未验证任何模型可用性">未接入</span>
            </div>
          </div>

          <div class="lg:col-span-7 bg-[#07080c] p-2.5 rounded-lg border border-[#191c26] shadow-[inset_0_2px_5px_rgba(0,0,0,0.85)] flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
            <div class="flex flex-col gap-1.5 flex-1 min-w-0">
              <div class="flex items-center justify-between">
                <span class="font-mono gw-type-body-sm text-[#857d6d]r">图片模型 (卡片生图 / LATENT DIFFUSION)</span>
                <span class="font-mono gw-type-body-sm text-amber-300" data-gw-degradation="not_integrated" title="未读取任何真实随机种子">未读取种子</span>
              </div>
              <div class="relative flex items-center justify-between bg-[#11131a] px-3 py-2 rounded border border-[#232734] hover:border-primary/40 transition-all cursor-pointer">
                <div class="flex items-center gap-2 truncate flex-1 min-w-0 pointer-events-none">
                  ${iconSvg('view_in_ar', 'w-4 h-4 text-primary shrink-0')}
                  <span class="font-mono gw-type-body-md font-bold text-[#f5efe5] truncate">${esc(imgModelDisplay)}</span>
                </div>
                <select class="absolute inset-0 opacity-0 cursor-pointer w-full h-full z-10" data-stage-model="asset_image" aria-label="选择图片模型">
                  ${imgModelOptions}
                </select>
                ${iconSvg('expand_more', 'w-4 h-4 text-[#6e7587] shrink-0 pointer-events-none')}
              </div>
            </div>

            <div class="flex items-center gap-2 pt-2 sm:pt-0 shrink-0">
              <button class="px-3 py-2 rounded-lg bg-[#141720] hover:bg-[#1a1e2a] text-[#dfc384] border border-[#2c3242] shadow-[0_3px_8px_rgba(0,0,0,0.6),inset_0_1px_1px_rgba(255,255,255,0.08)] active:translate-y-px transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-assets-refresh data-pipeline-id="${esc(pipeline.pipeline_id)}" ${extracting ? 'disabled' : ''}>
                ${iconSvg('refresh', `w-3.5 h-3.5 ${extracting ? 'animate-spin' : ''}`)}
                <span class="font-mono gw-type-body-md font-boldr">${extracting ? '提取中...' : '重新提取'}</span>
              </button>
              <button class="px-4 py-2 rounded-lg bg-gradient-to-b from-[#fcdf9d] to-[#dfc384] text-[#1a1308] font-bold shadow-[0_4px_12px_rgba(223,195,132,0.35),inset_0_1px_1px_rgba(255,255,255,0.6)] active:translate-y-px transition-all flex items-center gap-1.5 hover:brightness-105 cursor-pointer" type="button" data-stage-retry="assets" data-pipeline-id="${esc(pipeline.pipeline_id)}">
                ${iconSvg('bolt', 'w-4 h-4 text-current')}
                <span class="font-mono gw-type-body-md font-boldr">重新执行</span>
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- CONTROL DECK: 分类过滤胶囊带 -->
      <div class="flex flex-wrap items-center justify-between gap-4 px-1">
        <div class="flex flex-col gap-0.5">
          <div class="flex items-center gap-2.5">
            <h2 class="font-display gw-type-headline-sm font-medium text-[#f4eee4]">${totalCount ? `${totalCount} 项设定资产` : '剧本资产尚未提取'}</h2>
            <span class="px-2 py-0.5 rounded bg-[#13161f] text-[#827a6c] font-mono gw-type-body-sm border border-[#212634]">AUTO-PARSED</span>
          </div>
          <p class="gw-type-body-sm text-[#9c9484]">文本模型提取角色骨架、场景构筑和关键道具设定档案；图片模型负责渲染视觉锚定卡片。</p>
        </div>
        ${totalCount ? assetGroupFilter(pipeline, data) : ''}
      </div>

      <!-- SLIDER RACK: 资产卡片横向滑轨机架 -->
      <div class="relative w-full">
        <div class="absolute left-0 top-0 bottom-0 w-6 bg-gradient-to-r from-[#090a0d] to-transparent pointer-events-none z-10"></div>
        <div class="absolute right-0 top-0 bottom-0 w-8 bg-gradient-to-l from-[#090a0d] to-transparent pointer-events-none z-10"></div>
        
        ${totalCount ? (visibleAssets.length ? `
          <div class="w-full flex gap-4 overflow-x-auto pb-4 pt-1 px-1 scroll-smooth" id="cardsSliderRack">
            ${visibleAssets.map((item, idx) => assetCard(item, pipeline, idx)).join('')}
          </div>
        ` : `
          <div class="w-full py-16 flex flex-col items-center justify-center gap-3 bg-[#0a0c10] rounded-xl border border-white/5 text-neutral-400">
            ${iconSvg('inbox', 'w-8 h-8 text-neutral-600')}
            <span class="font-mono gw-type-body-md">当前分组暂无资产</span>
          </div>
        `) : `
          <div class="w-full py-16 px-6 bg-[#0b0d12] rounded-xl border border-[#1f232e] shadow-[0_12px_28px_rgba(0,0,0,0.8)] flex flex-col items-center justify-center text-center gap-4 relative">
            <span class="milled-screw absolute top-3 left-3"></span>
            <span class="milled-screw absolute top-3 right-3"></span>
            <div class="w-14 h-14 rounded-full bg-[#141720] border border-primary/30 flex items-center justify-center shadow-[0_0_16px_rgba(223,195,132,0.2)]">
              ${iconSvg('auto_fix_high', 'w-7 h-7 text-primary')}
            </div>
            <div class="flex flex-col gap-1">
              <h3 class="font-display gw-type-headline-sm font-medium text-[#f2ede4]">从已确认剧本提取资产</h3>
              <p class="gw-type-body-sm text-[#9c9484] max-w-md">点击“重新提取”或“根据剧本生成资产”，由资产提取 agent 读取剧本并创建可编辑的提示词卡片。</p>
            </div>
            <button class="px-5 py-2.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#c7a760] text-[#1a1308] font-mono gw-type-body-md font-bold shadow-[0_6px_20px_rgba(223,195,132,0.4),inset_0_1px_1px_rgba(255,255,255,0.7)] hover:brightness-105 active:translate-y-px transition-all flex items-center gap-2 cursor-pointer" type="button" data-assets-refresh data-pipeline-id="${esc(pipeline.pipeline_id)}">
              ${iconSvg('play_arrow', 'w-4 h-4 text-current')}
              <span>根据剧本提取资产</span>
            </button>
          </div>
        `}
      </div>

      <!-- FOOTER DOCK: 机械平移滑轨 (TRACK NAV) + 核心温度 + 香槟金批量大按键 -->
      <footer class="w-full bg-[#0b0d12] rounded-xl border border-[#1f232e] p-3.5 shadow-[0_10px_26px_rgba(0,0,0,0.85),inset_0_1px_1px_rgba(255,255,255,0.06)] flex flex-col md:flex-row items-center justify-between gap-4 mt-auto">
        <div class="flex-1 w-full flex items-center gap-3">
          <div class="flex items-center gap-1.5 text-[#857e6f]">
            ${iconSvg('tune', 'w-4 h-4 text-[#857e6f]')}
            <span class="font-mono gw-type-body-smr font-bold">TRACK NAV</span>
          </div>
          <div class="relative flex-1 h-3 bg-[#050608] rounded-full p-0.5 border border-[#1a1d27] shadow-[inset_0_2px_4px_rgba(0,0,0,0.95)] cursor-pointer" id="hardwareTrack" title="点击或拖动滑块平移卡片机架">
            <div class="h-full bg-gradient-to-r from-primary/30 to-primary rounded-full w-1/4 shadow-[0_0_8px_#dfc384]" id="trackFilament"></div>
            <div class="absolute top-1/2 -translate-y-1/2 left-[25%] -translate-x-1/2 w-8 h-5 rounded bg-[#181c26] border border-[#313749] shadow-[0_2px_6px_rgba(0,0,0,0.8),inset_0_1px_1px_rgba(255,255,255,0.25)] flex items-center justify-center cursor-grab active:cursor-grabbing hover:scale-105 transition-transform" id="trackThumb">
              <div class="w-3 h-0.5 bg-primary rounded-full shadow-[0_0_4px_#dfc384]"></div>
            </div>
          </div>
          <span class="font-mono gw-type-body-sm text-[#736c5e]">BAY POS: 01-${String(Math.min(visibleAssets.length, 6)).padStart(2, '0')} / ${String(totalCount).padStart(2, '0')}</span>
        </div>

        <div class="flex items-center gap-3 w-full md:w-auto justify-end">
          <div class="hidden sm:flex items-center gap-2 bg-[#050608] px-3 py-1.5 rounded-lg border border-[#191c26] shadow-[inset_0_2px_4px_rgba(0,0,0,0.85)]">
            ${iconSvg('thermostat', 'w-4 h-4 text-[#ffb689]')}
            <span class="font-mono gw-type-body-sm text-[#736c5f]">CORE TEMP</span>
            <span class="font-mono gw-type-body-sm text-[#ffb689] font-bold">58°C OPTIMAL</span>
          </div>

          <button class="px-4 py-2.5 rounded-lg bg-[#141722] hover:bg-[#1c2230] border border-[#2e3447] text-[#c9cbd2] font-mono gw-type-body-md font-semibold shadow-[0_2px_8px_rgba(0,0,0,0.5)] active:translate-y-px transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-goto-step="script" title="返回剧本生成">
            ${iconSvg('arrow_back', 'w-3.5 h-3.5 text-[#8b909f]')}
            <span>上一步：剧本</span>
          </button>

          <button class="px-5 py-2.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#c7a760] text-[#1a1308] font-mono gw-type-body-md font-bold shadow-[0_6px_20px_rgba(223,195,132,0.4),inset_0_1px_1px_rgba(255,255,255,0.7)] hover:brightness-105 active:translate-y-px active:shadow-[inset_0_2px_6px_rgba(0,0,0,0.7)] transition-all flex items-center gap-2 cursor-pointer ${pendingCount === 0 ? 'opacity-70' : ''}" type="button" data-batch-generate-assets data-pipeline-id="${esc(pipeline.pipeline_id)}">
            ${iconSvg('playlist_play', 'w-4 h-4 text-current')}
            <span class="font-body">${pendingCount > 0 ? `批量生成剩余 ${pendingCount} 项资产` : '已全部生成完毕'}</span>
          </button>

          <button class="px-4 py-2.5 rounded-lg bg-gradient-to-r from-[#1c2233] to-[#252c40] hover:from-[#232b40] hover:to-[#2e3752] border border-[#dfc384]/40 text-[#dfc384] font-mono gw-type-body-md font-bold shadow-[0_4px_14px_rgba(0,0,0,0.6)] active:translate-y-px transition-all flex items-center gap-1.5 cursor-pointer group" type="button" data-goto-step="video" title="确认资产并进入分镜脚本">
            <span>下一步：分镜</span>
            ${iconSvg('arrow_forward', 'w-3.5 h-3.5 text-primary group-hover:translate-x-0.5 transition-transform')}
          </button>
        </div>
      </footer>

      <!-- 底板信息条 -->
      <div class="w-full bg-[#060709] border-t border-[#14161f] py-2 px-6 flex items-center justify-between gw-type-body-sm font-mono text-[#575246]">
        <div class="flex items-center gap-3">
          <span class="text-[#7c7567]">GODS' WORKBENCH ARCHITECTURE</span>
          <span>SYS-REV 4.8.2-NEO</span>
        </div>
        <div class="flex items-center gap-4">
          <span>CLOCK: ATOMIC LOCK 10MHz</span>
          <span>© 2026 ALL RIGHTS RESERVED</span>
        </div>
      </div>

      <input id="asset-upload-input" type="file" accept="image/*" hidden>
    </section>`;
  }

  function storyboardModelSelector(pipeline, stage) {
    const spec = STAGES.find(item => item.key === stage.stage) || {};
    const modelKind = stage.modelKind || spec.modelKind || 'text';
    const options = modelOptions(modelKind);
    const selected = selectedProviderModel(pipeline, stage.stage, modelKind);
    return `<div class="flex items-center bg-[#07080c] border border-[#242735] px-3 py-1.5 rounded-lg shadow-inner gap-2.5">
      <span class="font-mono gw-type-body-sm text-[#717688]r">文本推理模型</span>
      <select class="bg-[#14161f] border border-gold-primary/30 text-gold-light font-mono gw-type-body-md font-semibold rounded px-2 py-0.5 focus:outline-none focus:border-gold-primary cursor-pointer" data-stage-model="video" aria-label="选择文本推理模型" ${options.length ? '' : 'disabled'}>
        ${options.length ? options.map(item => `<option value="${esc(`${item.provider_id}:::${item.model}`)}"${item.provider_id === selected.provider_id && item.model === selected.model ? ' selected' : ''}>${esc(item.provider_name)} / ${esc(item.label)}</option>`).join('') : '<option>未配置模型</option>'}
      </select>
      <div class="h-4 w-px bg-[#20232f]"></div>
      <div class="flex items-center gap-1.5 pl-0.5">
        <span class="w-2 h-2 rounded-full bg-amber-400 shadow-[0_0_8px_#f59e0b] animate-pulse"></span>
        <span class="font-mono gw-type-body-sm text-amber-300 font-boldr" data-gw-degradation="not_integrated" title="未接入任何模型服务，无法判定就绪状态">未接入</span>
      </div>
    </div>`;
  }

  function storyboardOutlineCard(label, key, num, value, placeholder, tag) {
    return `<div class="w-full bg-[#11131a] rounded-lg p-3 border border-outline hover:border-gold-primary/40 transition-all shadow-md group" data-outline-field-wrap="${esc(key)}">
      <div class="flex items-center justify-between pb-2 border-b border-[#181a24]">
        <div class="flex items-center gap-2">
          <span class="font-mono gw-type-body-sm px-2 py-0.5 bg-gold-primary/20 text-gold-light font-extrabold border border-gold-primary/40 rounded">${esc(num)}</span>
          <span class="font-display font-bold gw-type-body-md text-gold-light">${esc(label)}</span>
          <span class="font-mono gw-type-body-sm text-[#6d7283]">${esc(tag)}</span>
        </div>
        <button class="flex items-center gap-1 px-2 py-0.5 bg-[#090a0e] hover:bg-[#161822] rounded border border-[#20232e] text-[#8e93a4] hover:text-gold-light font-mono gw-type-body-md transition-all cursor-pointer" type="button" data-outline-expand aria-expanded="true">
          <span>收起</span>
          ${iconSvg('unfold_more', 'w-3 h-3 text-gold-dim')}
        </button>
      </div>
      <div class="outline-field-body pt-2.5">
        <div class="bg-[#08090d] p-2.5 rounded border border-[#181a24] shadow-inner">
          <textarea class="w-full bg-transparent border-0 text-[#d4d8e5] gw-type-body-sm leading-relaxed font-body focus:outline-none focus:ring-0 resize-y" data-outline-field="${esc(key)}" rows="3" placeholder="${esc(placeholder)}">${esc(value)}</textarea>
        </div>
      </div>
    </div>`;
  }

  function storyboardActsCard(acts) {
    return `<div class="w-full bg-[#11131a] rounded-lg p-3 border border-outline hover:border-gold-primary/40 transition-all shadow-md" data-outline-field-wrap="fiveActs">
      <div class="flex items-center justify-between pb-2 border-b border-[#181a24]">
        <div class="flex items-center gap-2">
          <span class="font-mono gw-type-body-sm px-2 py-0.5 bg-[#1e2230] text-gold-light font-extrabold border border-[#2e3448] rounded">04</span>
          <span class="font-display font-bold gw-type-body-md text-gold-light">五幕结构 (每行一条逐条展开)</span>
          <span class="font-mono gw-type-body-sm text-[#6d7283]">5-ACT DRAMATIC BEATS</span>
        </div>
        <button class="flex items-center gap-1 px-2 py-0.5 bg-[#090a0e] hover:bg-[#161822] rounded border border-[#20232e] text-[#8e93a4] hover:text-gold-light font-mono gw-type-body-md transition-all cursor-pointer" type="button" data-outline-expand aria-expanded="true">
          <span>收起</span>
          ${iconSvg('unfold_more', 'w-3 h-3 text-gold-dim')}
        </button>
      </div>
      <div class="outline-field-body flex flex-col gap-2 pt-2.5">
        ${acts.map((act, index) => `<div class="flex items-start gap-2.5 bg-[#08090d] p-2 rounded border border-[#181a24] shadow-inner">
          <span class="font-mono gw-type-body-sm ${index === 1 ? 'bg-gold-primary/20 text-gold-light border-gold-primary/40' : index === 2 ? 'bg-amber-500/20 text-amber-300 border-amber-500/40' : 'bg-[#161822] text-[#8c91a4] border-[#252838]'} border px-2 py-0.5 rounded shrink-0 font-bold">第 ${index + 1} 幕</span>
          <textarea class="flex-1 bg-transparent border-0 text-[#c2c6d4] gw-type-body-sm leading-relaxed focus:outline-none focus:ring-0 resize-y" data-outline-act="${index}" rows="2" placeholder="第 ${index + 1} 幕推进情节">${esc(act)}</textarea>
        </div>`).join('')}
      </div>
    </div>`;
  }

  function storyboardShotCard(shot, index) {
    return `<article class="storyboard-shot-card bg-gradient-to-b from-[#14161f] to-[#0c0d12] border border-[#222634] rounded-xl p-4 shadow-[0_8px_20px_rgba(0,0,0,0.7)] flex flex-col gap-3 group">
      <!-- 头部：镜号、时长、镜头类型 -->
      <div class="flex items-center justify-between pb-2 border-b border-[#1c1f2b]">
        <div class="flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-gold-primary shadow-[0_0_6px_#dfc384]"></span>
          <span class="font-mono gw-type-body-sm font-bold text-gold-light">分镜 #${String(index + 1).padStart(2, '0')}</span>
          <span class="font-mono gw-type-body-sm px-1.5 py-0.5 bg-[#1b1e2a] text-[#a0a5b8] border border-[#2b3042] rounded">${esc(shot.shotType || 'A')} 类镜头</span>
        </div>
        <div class="flex items-center gap-1.5 bg-[#090a0e] px-2.5 py-0.5 rounded border border-[#1f222e]">
          ${iconSvg('timer', 'w-3.5 h-3.5 text-gold-dim')}
          <span class="font-mono gw-type-body-sm text-gold-primary font-bold">${Number(shot.duration || 8)}s</span>
        </div>
      </div>

      <!-- 参数调整格栅：标题、机位、景别、运动、转场 -->
      <div class="grid grid-cols-2 sm:grid-cols-5 gap-2 font-mono gw-type-body-sm">
        <div class="flex flex-col gap-1 col-span-2 sm:col-span-1">
          <span class="gw-type-body-sm text-[#6d7283]">镜头标题</span>
          <input class="bg-[#090b10] border border-[#222532] focus:border-gold-primary/50 text-[#e1e4ed] px-2 py-1 rounded gw-type-body-md focus:outline-none" data-shot-field="title" data-shot-id="${esc(shot.id)}" value="${esc(shot.title)}" placeholder="镜头简述">
        </div>
        <div class="flex flex-col gap-1">
          <span class="gw-type-body-sm text-[#6d7283]">机位视点</span>
          <input class="bg-[#090b10] border border-[#222532] focus:border-gold-primary/50 text-[#e1e4ed] px-2 py-1 rounded gw-type-body-md focus:outline-none" data-shot-field="camera" data-shot-id="${esc(shot.id)}" value="${esc(shot.camera)}" placeholder="如 平视/仰拍">
        </div>
        <div class="flex flex-col gap-1">
          <span class="gw-type-body-sm text-[#6d7283]">景别规模</span>
          <input class="bg-[#090b10] border border-[#222532] focus:border-gold-primary/50 text-[#e1e4ed] px-2 py-1 rounded gw-type-body-md focus:outline-none" data-shot-field="scene" data-shot-id="${esc(shot.id)}" value="${esc(shot.scene)}" placeholder="如 特写/中景">
        </div>
        <div class="flex flex-col gap-1">
          <span class="gw-type-body-sm text-[#6d7283]">运镜动作</span>
          <input class="bg-[#090b10] border border-[#222532] focus:border-gold-primary/50 text-[#e1e4ed] px-2 py-1 rounded gw-type-body-md focus:outline-none" data-shot-field="movement" data-shot-id="${esc(shot.id)}" value="${esc(shot.movement)}" placeholder="如 推进/固定">
        </div>
        <div class="flex flex-col gap-1">
          <span class="gw-type-body-sm text-[#6d7283]">转场意图</span>
          <input class="bg-[#090b10] border border-[#222532] focus:border-gold-primary/50 text-[#e1e4ed] px-2 py-1 rounded gw-type-body-md focus:outline-none" data-shot-field="transition" data-shot-id="${esc(shot.id)}" value="${esc(shot.transition)}" placeholder="如 硬切/闪白">
        </div>
      </div>

      <!-- 关键节拍描述区 -->
      <div class="flex flex-col gap-1">
        <div class="flex items-center justify-between gw-type-body-sm font-mono text-[#6d7283]">
          <span>关键节拍与剧本内容 (KEY BEATS)</span>
          <span>SHOT-ID: ${esc(shot.id)}</span>
        </div>
        <div class="bg-[#07080c] p-2.5 rounded-lg border border-[#1a1c26] shadow-inner">
          <textarea class="w-full bg-transparent border-0 text-[#d4d8e5] gw-type-body-sm leading-relaxed focus:outline-none focus:ring-0 resize-y" data-shot-field="content" data-shot-id="${esc(shot.id)}" rows="3" placeholder="填写该镜头的关键动作、台词与身位节拍">${esc(shot.content || shot.beats)}</textarea>
        </div>
      </div>
    </article>`;
  }

  function storyboardSubStep1Markup(pipeline, board, fields, source, ready) {
    return `<div class="grid grid-cols-1 xl:grid-cols-12 gap-5 w-full items-start">
      <!-- LEFT BAY: RAW SCREENPLAY TELEMETRY MONITOR (xl:col-span-5) -->
      <section class="xl:col-span-5 bg-gradient-to-b from-[#14161f] to-[#0c0d12] rounded-xl p-4 border border-outline shadow-[0_16px_36px_rgba(0,0,0,0.8)] flex flex-col gap-3">
        <div class="flex items-center justify-between pb-2 border-b border-[#1f222e]">
          <div class="flex items-center gap-2">
            <div class="w-2 h-2 rounded-full bg-gold-primary shadow-[0_0_8px_#dfc384]"></div>
            <span class="font-display font-bold gw-type-body-md text-whiter">原始剧本监视器</span>
            <span class="font-mono gw-type-body-sm text-gold-dim">[CANON-TXT-01]</span>
          </div>
          <div class="flex items-center gap-2">
            <button class="flex items-center gap-1 px-2.5 py-0.5 bg-[#11131a] hover:bg-[#1a1d28] border border-[#2b2e3c] rounded text-gold-light font-mono gw-type-body-md transition-all shadow-sm" type="button" data-goto-step="script" title="返回剧本生成修改源文本">
              ${iconSvg('history_edu', 'w-3 h-3 text-gold-dim')}
              <span>来源脚本</span>
            </button>
          </div>
        </div>

        <div class="w-full bg-[#06070a] border border-[#1a1c26] rounded-lg p-4 shadow-[inset_0_3px_10px_rgba(0,0,0,0.95),inset_0_1px_2px_rgba(0,0,0,0.9)] overflow-y-auto max-h-[640px] flex flex-col gap-3 font-mono gw-type-body-sm leading-relaxed select-text">
          <div class="p-3 bg-gradient-to-b from-[#0f1118] to-[#090b10] border border-[#222634] rounded shadow-inner flex flex-col gap-1.5">
            <div class="flex items-center justify-between text-gold-light font-bold">
              <span class="gw-type-body-md font-display">${esc(pipeline.title ? `# 《${pipeline.title}》` : '# 剧集创作源')}</span>
              <span class="gw-type-body-sm text-[#787d8e] bg-[#050608] px-2 py-0.5 rounded border border-[#191b24]">EP-CANON</span>
            </div>
            <div class="text-[#a4a8b8] gw-type-body-sm"><span class="text-gold-primary font-semibold">&gt; 种子设定：</span>${esc(pipeline.seed || '尚未设定种子')}</div>
          </div>

          <div class="prose-screenplay flex flex-col gap-3">
            ${formatScreenplayHtml(source, pipeline)}
          </div>
        </div>

        <div class="bg-[#07080b] border border-[#181a24] p-2.5 rounded-lg shadow-inner flex items-center justify-between">
          <div class="flex items-center gap-2">
            ${iconSvg('graphic_eq', 'w-4 h-4 text-gold-dim')}
            <span class="font-mono gw-type-body-sm text-[#767b8d]">DENSITY INDEX</span>
          </div>
          <div class="flex items-center gap-2">
            <div class="flex gap-1">
              <div class="w-1.5 h-3 bg-gold-primary rounded-xs"></div>
              <div class="w-1.5 h-3 bg-gold-primary rounded-xs"></div>
              <div class="w-1.5 h-3 bg-gold-primary rounded-xs"></div>
              <div class="w-1.5 h-3 bg-amber-400 rounded-xs"></div>
              <div class="w-1.5 h-3 bg-[#1e222e] rounded-xs"></div>
            </div>
            <span class="font-mono gw-type-body-sm text-gold-light font-bold">+2.4 dB (HIGH-DENSITY)</span>
          </div>
        </div>
      </section>

      <!-- RIGHT BAY: STRUCTURED OUTLINE DECOMPOSITION RACK (xl:col-span-7) -->
      <section class="xl:col-span-7 bg-gradient-to-b from-[#14161f] to-[#0c0d12] rounded-xl p-4 border border-outline shadow-[0_16px_36px_rgba(0,0,0,0.8)] flex flex-col gap-3">
        <div class="flex items-center justify-between pb-2 border-b border-[#1f222e]">
          <div class="flex items-center gap-2">
            <div class="w-2 h-2 rounded-full bg-gold-primary shadow-[0_0_8px_#dfc384]"></div>
            <span class="font-display font-bold gw-type-body-md text-whiter">大纲 (可编辑)</span>
            <span class="font-mono gw-type-body-sm text-gold-dim">PARSED STRUCTURE</span>
          </div>
          <div class="flex items-center gap-2">
            <div class="flex items-center gap-1.5 bg-[#08090d] border border-[#232634] px-2 py-0.5 rounded shadow-inner">
              <span class="w-1.5 h-1.5 rounded-full ${ready ? 'bg-emerald-400 shadow-[0_0_6px_#34d399]' : 'bg-amber-400'}"></span>
              <span class="font-mono gw-type-body-sm ${ready ? 'text-emerald-400' : 'text-amber-300'} font-semibold">${ready ? '字段已对齐' : '待生成大纲'}</span>
            </div>
          </div>
        </div>

        <div class="flex flex-col gap-3">
          ${storyboardOutlineCard('核心冲突', 'coreConflict', '01', fields.coreConflict, '根据剧本提炼核心冲突与危机...', 'CORE_CONFLICT')}
          ${storyboardOutlineCard('主角动机', 'protagonistMotivation', '02', fields.protagonistMotivation, '主角内在与外在驱动力...', 'PROTAGONIST_DRIVE')}
          ${storyboardOutlineCard('信息增量', 'informationGain', '03', fields.informationGain, '本集递进揭露的关键世界观与反转...', 'LORE_DISCLOSURE')}
          ${storyboardActsCard(fields.fiveActs)}
          ${storyboardOutlineCard('场景质量方向', 'sceneQuality', '05', fields.sceneQuality, '光影风格、运镜速度与可拍性指导...', 'SCENE_QUALITY')}
        </div>

        <div class="w-full bg-[#07080b] border border-[#1a1c26] p-2.5 rounded-lg shadow-inner flex flex-col sm:flex-row items-center justify-between gap-3 mt-2">
          <div class="flex items-center gap-2 font-mono gw-type-body-sm text-[#8e93a4]">
            <span class="material-symbols-outlined gw-type-body-md text-gold-dim">tune</span>
            <span>大纲状态：${ready ? '<b class="text-emerald-400">校验通过，可进入拆分</b>' : '<b class="text-amber-400">待校验生成</b>'}</span>
          </div>
          <div class="flex items-center gap-2">
            <button class="px-5 py-2 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#b89547] text-[#120f05] font-display font-bold gw-type-body-md shadow-[0_0_18px_rgba(223,195,132,0.4),0_4px_10px_rgba(0,0,0,0.8),inset_0_1px_1px_rgba(255,255,255,0.6)] hover:brightness-110 active:scale-[0.98] transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-outline-generate>
              ${iconSvg('auto_awesome', 'w-4 h-4 text-[#120f05]')}
              <span>${ready ? '重新生成大纲' : '生成结构化大纲'}</span>
            </button>
            ${ready ? `<button class="px-4 py-2 rounded-lg bg-[#1a1e2a] hover:bg-[#23293a] border border-gold-primary/30 text-gold-light font-display font-bold gw-type-body-md transition-all flex items-center gap-1 cursor-pointer" type="button" data-storyboard-substep="2">
              <span>前往步骤 2 拆分分镜</span>
              ${iconSvg('chevron_right', 'w-3.5 h-3.5 text-gold-primary')}
            </button>` : ''}
          </div>
        </div>
      </section>
    </div>`;
  }

  function storyboardSubStep2Markup(pipeline, board, ready) {
    const shots = board.shots || [];
    const totalDuration = shots.reduce((sum, s) => sum + Number(s.duration || 8), 0);
    return `<section class="w-full bg-gradient-to-b from-[#14161f] to-[#0c0d12] rounded-xl p-5 border border-outline shadow-[0_16px_36px_rgba(0,0,0,0.8)] flex flex-col gap-4">
      <!-- 顶栏信息与统计条 -->
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 pb-3 border-b border-[#1f222e]">
        <div class="flex items-center gap-3">
          <div class="w-2.5 h-7 rounded-full bg-gradient-to-b from-gold-light to-gold-dim shadow-[0_0_10px_rgba(223,195,132,0.5)]"></div>
          <div class="flex flex-col">
            <div class="flex items-center gap-2">
              <h3 class="font-display gw-type-body-lg font-bold text-white">步骤 2 / 具体镜头节拍拆解</h3>
              <span class="font-mono gw-type-body-sm px-2 py-0.5 bg-gold-primary/20 text-gold-light border border-gold-primary/40 rounded font-bold">${shots.length} 镜已拆分</span>
            </div>
            <span class="font-mono gw-type-body-sm text-[#787d8e]">根据步骤1大纲，拆分具体机位、景别、运动、转场与关键节拍</span>
          </div>
        </div>

        <div class="flex items-center gap-3 flex-wrap">
          <div class="flex items-center gap-2 bg-[#090a0e] px-3 py-1.5 rounded-lg border border-[#1f222e] font-mono gw-type-body-sm">
            <span class="text-[#6d7283]">预估总片长:</span>
            <span class="text-gold-light font-bold">${totalDuration} 秒</span>
          </div>
          <button class="px-4 py-1.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#b89547] text-[#120f05] font-display font-bold gw-type-body-md shadow hover:brightness-105 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer ${ready ? '' : 'opacity-60'}" type="button" data-shots-generate ${ready ? '' : 'disabled'}>
            ${iconSvg('movie_edit', 'w-3.5 h-3.5 text-[#120f05]')}
            <span>${shots.length ? '重新拆分分镜' : '确认大纲并拆分分镜'}</span>
          </button>
          ${shots.length ? `<button class="px-4 py-1.5 rounded-lg bg-[#1b1f2c] hover:bg-[#252b3d] border border-gold-primary/40 text-gold-light font-display font-bold gw-type-body-md transition-all flex items-center gap-1 cursor-pointer" type="button" data-storyboard-substep="3">
            <span>前往步骤 3 提交审核</span>
            ${iconSvg('chevron_right', 'w-3.5 h-3.5 text-gold-primary')}
          </button>` : ''}
        </div>
      </div>

      <!-- 镜头网格 / 空态机架 -->
      ${shots.length ? `<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        ${shots.map(storyboardShotCard).join('')}
      </div>` : `<div class="w-full py-16 px-6 bg-[#08090d] rounded-xl border border-dashed border-[#242836] flex flex-col items-center justify-center text-center gap-3 shadow-inner">
        ${iconSvg('view_timeline', 'w-12 h-12 text-gold-dim/40')}
        <div class="flex flex-col gap-1 max-w-md">
          <h4 class="font-display gw-type-body-md font-bold text-[#e1e4ed]">尚未生成分镜镜头组</h4>
          <p class="font-mono gw-type-body-sm text-[#6e7384]">请先在步骤 1 确认结构化大纲，然后点击上方“确认大纲并拆分分镜”，分镜师 Agent 将自动为您规划逐镜头参数。</p>
        </div>
        <button class="mt-2 px-5 py-2 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#b89547] text-[#120f05] font-display font-bold gw-type-body-md shadow hover:brightness-105 cursor-pointer ${ready ? '' : 'opacity-60'}" type="button" data-shots-generate ${ready ? '' : 'disabled'}>
          一键拆分具体分镜
        </button>
      </div>`}
    </section>`;
  }

  function storyboardSubStep3Markup(pipeline, board) {
    const review = board.review;
    const shots = board.shots || [];
    if (!review) {
      return `<section class="w-full bg-gradient-to-b from-[#14161f] to-[#0c0d12] rounded-xl p-6 border border-outline shadow-[0_16px_36px_rgba(0,0,0,0.8)] flex flex-col items-center justify-center text-center gap-4 py-16">
        <div class="relative w-14 h-14 rounded-full bg-gradient-to-br from-[#1e2230] to-[#0d0f15] border border-gold-primary/30 flex items-center justify-center shadow-lg">
          ${iconSvg('rate_review', 'w-7 h-7 text-gold-primary')}
          <span class="absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full bg-amber-400 shadow-[0_0_8px_#f59e0b] animate-ping"></span>
        </div>
        <div class="flex flex-col gap-1.5 max-w-lg">
          <h3 class="font-display gw-type-headline-sm font-medium text-white">步骤 3 / 分镜审片官审核与重拆</h3>
          <p class="font-mono gw-type-body-sm text-[#7e8498] leading-relaxed">Gods' Workbench 严格分镜审片官将对叙事清晰度、镜头连续性、可执行性、节奏控制力、资产一致性进行 0-100 分综合打分（80 分及格），并提供逐条针对性修改意见。</p>
        </div>
        <div class="flex items-center gap-3 mt-2">
          <button class="px-6 py-2.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#b89547] text-[#120f05] font-display font-bold gw-type-body-md shadow-[0_0_20px_rgba(223,195,132,0.4)] hover:brightness-110 active:scale-95 transition-all flex items-center gap-2 cursor-pointer ${shots.length ? '' : 'opacity-60'}" type="button" data-storyboard-review ${shots.length ? '' : 'disabled'}>
            ${iconSvg('auto_awesome', 'w-4 h-4 text-[#120f05]')}
            <span>提交严格分镜审片官审核</span>
          </button>
          <button class="px-4 py-2.5 rounded-lg bg-[#141722] hover:bg-[#1c2230] border border-[#2e3447] text-[#c9cbd2] font-mono gw-type-body-md font-semibold transition-all cursor-pointer" type="button" data-storyboard-substep="2">
            返回调整镜头
          </button>
        </div>
      </section>`;
    }

    const isPassed = review.status === 'passed';
    const dimensions = Array.isArray(review.dimensions) ? review.dimensions : [];
    const problems = Array.isArray(review.problems) && review.problems.length ? review.problems : review.issues || ['未检出明显问题。'];
    const suggestions = Array.isArray(review.suggestions) && review.suggestions.length ? review.suggestions : ['按既定分镜推进。'];
    const priorities = Array.isArray(review.priority) && review.priority.length ? review.priority : ['先落实关键连贯性修改。'];
    const summary = String(review.summary || review.overallVerdict || '分镜审核已返回');

    return `<section class="w-full bg-gradient-to-b from-[#14161f] to-[#0c0d12] rounded-xl p-5 border border-outline shadow-[0_16px_36px_rgba(0,0,0,0.8)] flex flex-col gap-4">
      <!-- 审核总览头卡 -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 items-center bg-[#07090e] p-4 rounded-xl border border-[#1e2230] shadow-inner">
        <!-- 综合得分 -->
        <div class="lg:col-span-3 flex flex-col items-center justify-center p-3 bg-[#0a0d14] rounded-lg border border-[#222738]">
          <span class="font-mono gw-type-body-sm text-[#6d7283]r">综合审片得分</span>
          <div class="flex items-baseline gap-1 my-1">
            <span class="font-mono gw-type-headline-lg font-black ${isPassed ? 'text-emerald-400' : 'text-amber-400'}">${esc(review.score)}</span>
            <span class="font-mono gw-type-body-sm text-[#555a6a]">/ 100</span>
          </div>
          <span class="decision-stamp ${isPassed ? 'passed' : 'failed'}">${isPassed ? '审核通过 PASSED' : '待修改 REVISE'}</span>
          <span class="font-mono gw-type-body-sm text-[#555a6a] mt-2">${esc(dateTime(review.at))}</span>
        </div>

        <!-- 审片官总评 -->
        <div class="lg:col-span-9 flex flex-col justify-center gap-2">
          <div class="flex items-center justify-between border-b border-[#181a24] pb-1.5">
            <div class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full ${isPassed ? 'bg-emerald-400' : 'bg-amber-400'}"></span>
              <span class="font-display gw-type-body-md font-bold text-gold-light">严格分镜审片官结论</span>
            </div>
            <span class="font-mono gw-type-body-sm text-[#6d7283]">REVIEW AGENT: MK-IV</span>
          </div>
          <p class="gw-type-body-sm text-[#cfd3e2] leading-relaxed select-text">${esc(summary)}</p>
        </div>
      </div>

      <!-- 五维专业评分雷达网格 -->
      <div class="flex flex-col gap-2">
        <span class="font-mono gw-type-body-sm text-gold-dim font-boldr">五维专业量化指标</span>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2.5">
          ${dimensions.map(item => {
            const key = String(item.key || item.name || '');
            const label = STORYBOARD_DIMENSION_LABELS[key] || key;
            const score = Number(item.score || 0);
            return `<div class="bg-[#090b10] p-3 rounded-lg border border-[#1e2230] shadow-inner flex flex-col justify-between gap-2">
              <div>
                <div class="flex items-center justify-between font-mono gw-type-body-sm pb-1 border-b border-[#151722]">
                  <strong class="text-white">${esc(label)}</strong>
                  <span class="${score >= 80 ? 'text-emerald-400' : 'text-amber-400'} font-bold">${score}</span>
                </div>
                <div class="w-full h-1 bg-[#12141c] rounded-full overflow-hidden my-2">
                  <div class="h-full ${score >= 80 ? 'bg-emerald-400' : 'bg-amber-400'} rounded-full" style="width:${Math.max(0, Math.min(100, score))}%"></div>
                </div>
              </div>
              <p class="gw-type-body-sm text-[#8e93a4] leading-relaxed line-clamp-3" title="${esc(item.comment)}">${esc(item.comment || '检查完成')}</p>
            </div>`;
          }).join('')}
        </div>
      </div>

      <!-- 问题清单与修改意见 (双舱) -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <!-- 主要问题与优先级 -->
        <div class="bg-[#090b10] p-4 rounded-xl border border-[#1e2230] flex flex-col gap-2 shadow-inner">
          <div class="flex items-center justify-between pb-1.5 border-b border-[#181a24]">
            <span class="font-display gw-type-body-sm font-bold text-amber-300 flex items-center gap-1.5">
              ${iconSvg('warning', 'w-3.5 h-3.5 text-amber-400')}
              <span>检出主要问题与高危项</span>
            </span>
            <span class="font-mono gw-type-body-sm text-[#6d7283]">${problems.length} 项</span>
          </div>
          <ul class="space-y-1.5 font-mono gw-type-body-sm text-[#cfd3e2]">
            ${problems.map(p => `<li class="flex items-start gap-1.5"><span class="text-amber-400 mt-0.5">•</span><span>${esc(p)}</span></li>`).join('')}
          </ul>
        </div>

        <!-- 针对性可执行建议 -->
        <div class="bg-[#090b10] p-4 rounded-xl border border-[#1e2230] flex flex-col gap-2 shadow-inner">
          <div class="flex items-center justify-between pb-1.5 border-b border-[#181a24]">
            <span class="font-display gw-type-body-sm font-bold text-emerald-300 flex items-center gap-1.5">
              ${iconSvg('auto_fix_high', 'w-3.5 h-3.5 text-emerald-400')}
              <span>修改建议与优化路径</span>
            </span>
            <span class="font-mono gw-type-body-sm text-[#6d7283]">${suggestions.length} 条</span>
          </div>
          <ul class="space-y-1.5 font-mono gw-type-body-sm text-[#cfd3e2]">
            ${suggestions.map(s => `<li class="flex items-start gap-1.5"><span class="text-emerald-400 mt-0.5">✓</span><span>${esc(s)}</span></li>`).join('')}
          </ul>
        </div>
      </div>

      <!-- 审片操作按键栏 -->
      <div class="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2 border-t border-[#181a24]">
        <div class="flex items-center gap-2">
          <button class="px-4 py-2 rounded-lg bg-[#181c28] hover:bg-[#202636] border border-[#2e3448] text-gold-light font-display gw-type-body-md font-bold transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-storyboard-review>
            ${iconSvg('refresh', 'w-3.5 h-3.5 text-gold-primary')}
            <span>重新提交审核</span>
          </button>
          <button class="px-4 py-2 rounded-lg bg-[#141722] hover:bg-[#1c2230] border border-[#2e3447] text-[#c9cbd2] font-mono gw-type-body-md transition-all cursor-pointer" type="button" data-storyboard-substep="2">
            返回调整镜头
          </button>
        </div>

        <div class="flex items-center gap-3">
          ${!isPassed ? `<button class="px-5 py-2 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#c7a760] text-[#1a1308] font-display gw-type-body-md font-bold shadow-[0_4px_14px_rgba(223,195,132,0.3)] hover:brightness-105 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-revise-storyboard>
            ${iconSvg('auto_fix_high', 'w-4 h-4 text-current')}
            <span>按审核意见一键重拆分镜</span>
          </button>` : ''}
          <button class="px-5 py-2 rounded-lg bg-gradient-to-b from-[#34d399] to-[#059669] text-white font-display gw-type-body-md font-bold shadow-[0_4px_14px_rgba(52,211,153,0.3)] hover:brightness-105 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-complete-storyboard>
            <span>确认分镜，进入视频脚本</span>
            ${iconSvg('arrow_forward', 'w-4 h-4 text-white')}
          </button>
        </div>
      </div>
    </section>`;
  }

  function storyboardMarkup(pipeline, stage, data) {
    const board = data.storyboard;
    const fields = outlineFields(board.outline);
    const source = storyboardSource(pipeline, data);
    const ready = outlineIsReady(board.outline);
    const shots = board.shots || [];
    const review = board.review || null;

    // 当前选中的子步骤：1 大纲规划，2 镜头节拍拆解，3 审片与重拆
    const subStep = state.storyboardSubStep === 1 || state.storyboardSubStep === 2 || state.storyboardSubStep === 3
      ? state.storyboardSubStep
      : review ? 3 : shots.length > 0 ? 2 : 1;

    return `<section class="w-full flex flex-col justify-between bg-chassis-bg min-h-screen text-[#e3e2e6] select-none" id="stage-storyboard">
      <main class="w-full max-w-[1680px] mx-auto p-4 flex flex-col gap-4">
        <!-- ===================================================================== -->
        <!-- TOP CONSOLE RACK: TELEMETRY & MODEL SELECTOR BAY                      -->
        <!-- ===================================================================== -->
        <header class="w-full bg-gradient-to-b from-[#171920] to-[#0f1115] rounded-xl p-4 border border-outline shadow-[0_12px_28px_rgba(0,0,0,0.75)] relative overflow-hidden">
          <div class="absolute inset-0 gold-radial-flare pointer-events-none"></div>

          <!-- Upper Row: Telemetry, Model Bay & Global Steps -->
          <div class="relative z-10 flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-3 border-b border-[#1f222d]">
            <!-- Left: Telemetry Block -->
            <div class="flex items-center gap-3">
              <div class="w-2.5 h-8 rounded-full bg-gradient-to-b from-gold-light to-gold-dim shadow-[0_0_12px_rgba(223,195,132,0.6)]"></div>
              <div class="flex flex-col">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="font-display gw-type-body-md text-gold-light font-boldr">步骤 03 / 分镜脚本</span>
                  <span class="text-gold-dim">·</span>
                  <span class="font-mono gw-type-body-sm text-[#8e93a4]">SHOT-PLANNING ENGINE v4.2 PRO</span>
                  <span class="font-mono gw-type-body-sm px-1.5 py-0.5 bg-gold-primary/20 text-gold-light border border-gold-primary/30 rounded">${ready ? 'PARSED' : 'STANDBY'}</span>
                </div>
                <div class="flex items-center gap-2 mt-0.5 gw-type-body-sm text-[#9a9da8]">
                  <span>提示词链路锁定：</span>
                  <span class="font-mono text-gold-light bg-[#0a0c10] px-2 py-0.5 rounded border border-[#21242e] flex items-center gap-1.5">
                    ${iconSvg('lock', 'w-3 h-3 text-gold-primary')}
                    剧集提示词库 · Outline And Shot Planning
                  </span>
                </div>
              </div>
            </div>

            <!-- Center/Right: Model Selector & Global Step Jumper -->
            <div class="flex items-center flex-wrap gap-3">
              ${storyboardModelSelector(pipeline, stage)}

              <!-- 全局步骤快速切换胶囊 -->
              <div class="flex items-center gap-1 p-1 bg-[#090b10] border border-[#222634] rounded-lg shadow-inner">
                <button class="px-2.5 py-1 rounded gw-type-body-md font-mono text-[#898e9e] hover:text-[#d1d5db] transition-all cursor-pointer" type="button" data-goto-step="script">01 剧本</button>
                <button class="px-2.5 py-1 rounded gw-type-body-md font-mono text-[#898e9e] hover:text-[#d1d5db] transition-all cursor-pointer" type="button" data-goto-step="assets">02 资产</button>
                <button class="relative px-3 py-1 rounded gw-type-body-md font-mono font-bold text-black bg-gradient-to-b from-[#fcdf9d] to-[#c9a65d] shadow-[0_0_12px_rgba(223,195,132,0.5),inset_0_1px_1px_rgba(255,255,255,0.4)] cursor-pointer" type="button" data-goto-step="video">
                  03 分镜
                </button>
                <button class="px-2.5 py-1 rounded gw-type-body-md font-mono text-[#898e9e] hover:text-[#d1d5db] transition-all cursor-pointer" type="button" data-goto-step="audio_compose">04 视频</button>
              </div>
            </div>
          </div>

          <!-- Lower Row: Sub-rail Segmented Capsule Deck & Metric Bar -->
          <div class="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-3 pt-3">
            <!-- Step Sub-rail Capsules (可点击切换 1 / 2 / 3) -->
            <div class="flex items-center gap-2 flex-wrap">
              <!-- Step 1 Capsule -->
              <button class="flex items-center gap-2 px-3 py-1 rounded transition-all cursor-pointer ${subStep === 1 ? 'bg-gradient-to-r from-gold-primary/25 to-gold-primary/10 border border-gold-primary/50 text-gold-light shadow-[0_0_10px_rgba(223,195,132,0.2)]' : 'bg-[#0c0d12] hover:bg-[#141620] border border-[#1f222e] text-[#808595]'}" type="button" data-storyboard-substep="1">
                ${iconSvg('check_circle', `w-3.5 h-3.5 ${ready ? 'text-gold-primary' : 'text-[#616677]'}`)}
                <span class="font-mono gw-type-body-md font-boldr">步骤 1 大纲规划</span>
                ${ready ? '<span class="font-mono gw-type-body-sm bg-gold-primary text-black font-extrabold px-1.5 py-0.5 rounded">DONE</span>' : ''}
              </button>

              <!-- Step 2 Capsule -->
              <button class="flex items-center gap-2 px-3 py-1 rounded transition-all cursor-pointer ${subStep === 2 ? 'bg-gradient-to-r from-gold-primary/25 to-gold-primary/10 border border-gold-primary/50 text-gold-light shadow-[0_0_10px_rgba(223,195,132,0.2)]' : 'bg-[#0c0d12] hover:bg-[#141620] border border-[#1f222e] text-[#808595]'}" type="button" data-storyboard-substep="2">
                ${iconSvg('movie_edit', 'w-3.5 h-3.5 text-current')}
                <span class="font-mono gw-type-body-smr">步骤 2 分镜节拍拆解</span>
                ${shots.length ? `<span class="font-mono gw-type-body-sm bg-[#1e2230] text-gold-light px-1.5 py-0.5 rounded font-bold">${shots.length} 镜</span>` : ''}
              </button>

              <!-- Step 3 Capsule -->
              <button class="flex items-center gap-2 px-3 py-1 rounded transition-all cursor-pointer ${subStep === 3 ? 'bg-gradient-to-r from-gold-primary/25 to-gold-primary/10 border border-gold-primary/50 text-gold-light shadow-[0_0_10px_rgba(223,195,132,0.2)]' : 'bg-[#0c0d12] hover:bg-[#141620] border border-[#1f222e] text-[#808595]'}" type="button" data-storyboard-substep="3">
                ${iconSvg('rate_review', 'w-3.5 h-3.5 text-current')}
                <span class="font-mono gw-type-body-smr">步骤 3 镜头机位与审片</span>
                ${review ? `<span class="font-mono gw-type-body-sm ${review.status === 'passed' ? 'bg-emerald-500/20 text-emerald-300' : 'bg-amber-500/20 text-amber-300'} px-1.5 py-0.5 rounded font-bold">${review.score}分</span>` : ''}
              </button>
            </div>

            <!-- Token Budget / Telemetry Gauge -->
            <div class="flex items-center gap-2.5 bg-[#08090d] border border-[#1d202b] px-3 py-1 rounded">
              <span class="font-mono gw-type-body-sm text-[#717688]">TOKEN BUDGET</span>
              <div class="w-20 h-1.5 bg-[#12141c] rounded-full overflow-hidden p-0.5">
                <div class="h-full w-1/4 bg-gold-primary rounded-full shadow-[0_0_6px_#dfc384]"></div>
              </div>
              <span class="font-mono gw-type-body-sm text-gold-light font-bold">12,480 / 128K</span>
            </div>
          </div>
        </header>

        <!-- ===================================================================== -->
        <!-- MAIN WORKBENCH VIEWPORT (根据子步骤渲染)                               -->
        <!-- ===================================================================== -->
        ${subStep === 1
          ? storyboardSubStep1Markup(pipeline, board, fields, source, ready)
          : subStep === 2
          ? storyboardSubStep2Markup(pipeline, board, ready)
          : storyboardSubStep3Markup(pipeline, board)}
      </main>

      <!-- ===================================================================== -->
      <!-- FOOTER DOCK (完整双向路由动作坞)                                      -->
      <!-- ===================================================================== -->
      <footer class="w-full bg-[#07080c] border-t border-[#1a1c26] px-6 py-3 shadow-[0_-8px_24px_rgba(0,0,0,0.85)] flex flex-col md:flex-row items-center justify-between gap-4 mt-6">
        <!-- 遥测与状态 -->
        <div class="flex items-center gap-3 font-mono gw-type-body-sm text-[#717688]">
          <div class="flex items-center gap-1.5 bg-[#0b0d13] px-2.5 py-1 rounded border border-[#1b1e2a]">
            <span class="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_6px_#34d399]"></span>
            <span>SHOT METRIC BUS: SYNC OK</span>
          </div>
          <span class="hidden sm:inline text-[#3a3f50]">|</span>
          <span class="hidden sm:inline">ACTS: 05 / 05</span>
          <span class="hidden sm:inline">BEATS: ${shots.length || 0} PTS</span>
        </div>

        <!-- 动作操作区 -->
        <div class="flex items-center gap-3 w-full md:w-auto justify-end">
          <!-- 上一步：资产生成 -->
          <button class="px-4 py-2.5 rounded-lg bg-[#141722] hover:bg-[#1c2230] border border-[#2e3447] text-[#c9cbd2] font-mono gw-type-body-md font-semibold shadow-[0_2px_8px_rgba(0,0,0,0.5)] active:translate-y-px transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-goto-step="assets" title="返回资产生成">
            ${iconSvg('arrow_back', 'w-3.5 h-3.5 text-[#8b909f]')}
            <span>上一步：资产生成</span>
          </button>

          <!-- 中间主行动键 (按当前子步骤动态展现) -->
          ${subStep === 1 ? `<button class="px-5 py-2.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#c7a760] text-[#1a1308] font-display gw-type-body-md font-bold shadow-[0_4px_16px_rgba(223,195,132,0.4)] hover:brightness-105 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-outline-generate>
            ${iconSvg('auto_awesome', 'w-4 h-4 text-[#1a1308]')}
            <span>${ready ? '更新大纲规划' : '生成大纲规划'}</span>
          </button>` : subStep === 2 ? `<button class="px-5 py-2.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#c7a760] text-[#1a1308] font-display gw-type-body-md font-bold shadow-[0_4px_16px_rgba(223,195,132,0.4)] hover:brightness-105 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-shots-generate ${ready ? '' : 'disabled'}>
            ${iconSvg('movie_edit', 'w-4 h-4 text-[#1a1308]')}
            <span>${shots.length ? '重新拆分分镜' : '拆分具体分镜'}</span>
          </button>` : review && review.status !== 'passed' ? `<button class="px-5 py-2.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#c7a760] text-[#1a1308] font-display gw-type-body-md font-bold shadow-[0_4px_16px_rgba(223,195,132,0.4)] hover:brightness-105 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-revise-storyboard>
            ${iconSvg('auto_fix_high', 'w-4 h-4 text-[#1a1308]')}
            <span>按意见一键重拆分镜</span>
          </button>` : `<button class="px-5 py-2.5 rounded-lg bg-gradient-to-b from-[#fcdf9d] via-[#dfc384] to-[#c7a760] text-[#1a1308] font-display gw-type-body-md font-bold shadow-[0_4px_16px_rgba(223,195,132,0.4)] hover:brightness-105 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer" type="button" data-storyboard-review ${shots.length ? '' : 'disabled'}>
            ${iconSvg('auto_awesome', 'w-4 h-4 text-[#1a1308]')}
            <span>提交分镜审核</span>
          </button>`}

          <!-- 下一步：视频脚本 -->
          <button class="px-4 py-2.5 rounded-lg bg-gradient-to-r from-[#1c2233] to-[#252c40] hover:from-[#232b40] hover:to-[#2e3752] border border-[#dfc384]/40 text-[#dfc384] font-mono gw-type-body-md font-bold shadow-[0_4px_14px_rgba(0,0,0,0.6)] active:translate-y-px transition-all flex items-center gap-1.5 cursor-pointer group" type="button" data-goto-step="audio_compose" title="前往视频脚本制作">
            <span>下一步：视频脚本</span>
            ${iconSvg('arrow_forward', 'w-3.5 h-3.5 text-primary group-hover:translate-x-0.5 transition-transform')}
          </button>
        </div>
      </footer>

      <!-- 底板信息条 -->
      <div class="w-full bg-[#060709] border-t border-[#14161f] py-2 px-6 flex items-center justify-between gw-type-body-sm font-mono text-[#575246]">
        <div class="flex items-center gap-3">
          <span class="text-[#7c7567]">GODS' WORKBENCH ARCHITECTURE</span>
          <span>SYS-REV 4.8.2-NEO</span>
        </div>
        <div class="flex items-center gap-4">
          <span>CLOCK: ATOMIC LOCK 10MHz</span>
          <span>© 2026 ALL RIGHTS RESERVED</span>
        </div>
      </div>
    </section>`;
  }

  function videoScriptMarkup(pipeline, stage, data) {
    const scripts = data.videoScripts;
    const selectedVideoAssets = scripts.filter(item => item.exportSelected && item.videoStatus === 'ready' && item.assetId);
    const exportRequestKey = videoRequestKey(pipeline, '__export__');
    const exportWaiting = videoRequests.has(exportRequestKey) || (data.videoExportStatus === 'running' && data.videoExportJobId && videoPolls.has(data.videoExportJobId));
    const exportContentUrl = safeVideoScriptUrl(data.videoExportUrl);
    const exportDownloadUrl = safeVideoScriptDownloadUrl(data.videoExportUrl);
    const busy = pendingFor(pipeline, 'video_scripts');
    const completed = scripts.filter(item => item.videoUrl && item.videoStatus !== 'running').length;
    const running = scripts.filter(item => item.videoStatus === 'running').length;
    const failed = scripts.filter(item => item.videoStatus === 'failed').length;
    const progress = scripts.length ? Math.round(completed / scripts.length * 100) : 0;
    const videoModel = selectedVideoModel(pipeline);
    const canRender = Boolean(videoModel.provider_id);
    const contextReady = videoContextReady(pipeline);
    return `<section class="video-script-rack ${statusClass(stage.status)}" id="stage-video-script" aria-label="视频脚本工作区">
      <div class="vs-panel">
        <div class="vs-master-head">
          <div><h2>步骤 04 / 视频脚本</h2><p>提示词链路：${esc(promptSource(pipeline, stage))} · <span class="vs-kicker">PER-SHOT PROMPT GENERATION</span></p></div>
          ${modelPicker(pipeline, stage)}${statusChip(stage.status)}
        </div>
        <div class="vs-agent"><div class="vs-agent-copy">
          ${iconSvg('precision_manufacturing', 'vs-agent-icon')}
          <div><h3>高级单镜提示词工程师 AGENT</h3><p>根据当前分镜，使用文本模型生成每个镜头的详细视频制作脚本。单镜头出片使用右侧视频模型。</p></div>
        </div><button class="btn primary" type="button" data-video-scripts-generate ${busy || !data.storyboard.shots.length ? 'disabled' : ''}>${busy ? '正在生成视频脚本…' : '生成详细视频脚本'}</button></div>
        ${busy ? '<p role="status" aria-live="polite">提示词工程师正在生成，请稍候。</p>' : ''}
      </div>
      <div class="vs-grid"><div class="video-script-list">
        ${scripts.length ? scripts.map((item, index) => {
          const contentUrl = safeVideoScriptUrl(item.videoUrl);
          const downloadUrl = safeVideoScriptDownloadUrl(item.videoUrl);
          const requestKey = videoRequestKey(pipeline, item.id);
          const statusLabel = videoStatusLabel(item);
          const waiting = videoRequests.has(requestKey) || (item.videoStatus === 'running' && item.videoJobId && videoPolls.has(item.videoJobId));
          const blocked = item.videoStatus === 'unknown';
          return `<article class="vs-panel video-script-card">
          <div class="vs-card-head"><h3>镜头组 #${String(index + 1).padStart(2, '0')}</h3><span class="vs-badge">绑定 ${esc(item.shotId)}</span><span class="vs-badge">${esc(statusLabel)}</span></div>
          <label class="vs-field">主提示词 (POSITIVE SCRIPT PROMPT)<textarea data-video-field="prompt" data-video-id="${esc(item.id)}" rows="4">${esc(item.prompt)}</textarea></label>
          <label class="vs-field">反向提示词 (NEGATIVE PROMPT)<textarea data-video-field="negative" data-video-id="${esc(item.id)}" rows="2">${esc(item.negative)}</textarea></label>
          <div class="vs-actions"><label>资产槽 / 文本引用<input data-video-field="assets" data-video-id="${esc(item.id)}" value="${esc(item.assets)}"></label>
            ${item.videoStatus === 'ready' && item.assetId ? `<label class="vs-export-select"><input type="checkbox" data-video-export-select="${esc(item.id)}" ${item.exportSelected ? 'checked' : ''}><span>纳入合并导出</span></label>` : ''}
            <button class="btn primary" type="button" data-video-generate="${esc(item.id)}" ${busy || !canRender || waiting || blocked || (!contextReady && !(item.videoStatus === 'submitting' && item.videoSubmission) && !(item.videoStatus === 'running' && item.videoJobId)) ? 'disabled' : ''}>${item.videoStatus === 'submitting' ? '恢复原请求' : item.videoStatus === 'running' ? (waiting ? '查询中…' : '继续查询') : item.videoUrl ? '重新生成视频' : '生成此镜头'}</button>
            ${item.videoStatus === 'running' && item.videoJobId ? `<button class="btn danger" type="button" data-video-cancel="${esc(item.id)}" ${waiting ? 'disabled' : ''}>取消任务</button>` : ''}
            ${downloadUrl ? `<a class="btn" href="${esc(downloadUrl)}" download>下载视频</a>` : ''}
          </div>
          ${item.videoStatus === 'submitting' ? '<p class="vs-task-note">提交结果待确认；恢复时会使用已保存的原始参数与幂等键，不会另建请求。</p>' : ''}
          ${item.videoStatus === 'unknown' ? '<p class="vs-task-note is-warning">服务端标记创建结果未知。为避免重复计费，已禁止自动重提；请先人工核实上游任务。</p>' : ''}
          ${item.videoRemoteMayContinue && item.videoJobStatus !== 'succeeded' ? `<p class="vs-task-note is-warning">${item.videoStatus === 'canceled' ? '本地任务已取消，但' : '本地未确认任务成功；'}上游任务可能继续运行或计费。</p>` : ''}
          ${item.videoError ? `<p class="vs-task-note is-warning">${esc(item.videoError)}</p>` : ''}
          ${contentUrl ? `<video class="vs-video-preview" controls preload="metadata" src="${esc(contentUrl)}">浏览器不支持视频预览。</video>` : ''}
        </article>`;
        }).join('') : `<div class="vs-panel vs-empty"><h3>等待逐镜视频脚本</h3><p>${data.storyboard.shots.length ? `已有 ${data.storyboard.shots.length} 个分镜，点击上方按钮生成详细视频脚本。` : '请先完成分镜拆分，再开始逐镜提示词编译。'}</p><button class="btn" type="button" data-goto-step="video">前往分镜脚本</button></div>`}
      </div><aside class="vs-rail" aria-label="视频生成控制机架">
        <section class="vs-panel"><h3>镜头出片进度 / SHOT OUTPUT</h3><div class="vs-dial" style="--vs-progress:${progress}" role="img" aria-label="已出片 ${completed} / ${scripts.length}"><div class="vs-dial-inner"><strong>${progress}%</strong><small>已生成镜头</small></div></div><span class="vs-readout">${completed} / ${scripts.length} SHOTS</span></section>
        <section class="vs-panel"><h3>任务状态 / RENDER STATUS</h3><dl class="vs-metrics"><dt>生成中</dt><dd>${running}</dd><dt>已出片</dt><dd>${completed}</dd><dt>失败待重试</dt><dd>${failed}</dd></dl></section>
        <section class="vs-panel"><h3>出片控制 / RENDER ENGINE</h3>${modelPicker(pipeline, {stage: 'video_render', modelKind: 'video', modelLabel: '视频模型'})}
          ${videoContextMarkup(pipeline)}
          <dl class="vs-metrics"><dt>单镜时长</dt><dd>5 秒</dd><dt>画幅</dt><dd>16:9</dd></dl>
          <p>运镜加速度、采样步数与种子锁定尚未对接，不作为生成参数提交。资产槽按文本引用随提示词发送，不自动附加参考图片。</p>
          ${!canRender ? '<p>暂无可用视频模型，请先在设置中配置。</p>' : ''}
        </section>
        <section class="vs-panel"><h3>合并导出 / LOCAL EXPORT</h3>
          <p>已选 ${selectedVideoAssets.length} 个本项目已生成镜头；导出在本机 FFmpeg 执行，不调用视频 Provider。</p>
          <button class="btn primary" type="button" data-video-export ${exportWaiting || ((!selectedVideoAssets.length || !contextReady) && !data.videoExportSubmission && !(data.videoExportStatus === 'running' && data.videoExportJobId)) ? 'disabled' : ''}>${data.videoExportStatus === 'submitting' ? '恢复原导出请求' : data.videoExportStatus === 'running' ? (exportWaiting ? '导出处理中…' : '继续查询导出') : '合并导出 720p / 30fps'}</button>
          ${data.videoExportStatus ? `<p class="vs-task-note">导出状态：${esc(data.videoExportStatus)}${data.videoExportJobId ? ` · ${esc(data.videoExportJobId)}` : ''}</p>` : ''}
          ${data.videoExportStatus === 'running' && data.videoExportJobId ? '<button class="btn danger" type="button" data-video-export-cancel>取消本地导出</button>' : ''}
          ${data.videoExportError ? `<p class="vs-task-note is-warning">${esc(data.videoExportError)}</p>` : ''}
          ${exportContentUrl ? `<video class="vs-video-preview" controls preload="metadata" src="${esc(exportContentUrl)}">浏览器不支持视频预览。</video><a class="btn" href="${esc(exportDownloadUrl)}" download>下载合并视频</a>` : ''}
          ${data.videoExportStatus === 'submitting' ? '<p class="vs-task-note">恢复将使用已保存的原选择和幂等键。</p>' : ''}
        </section>
      </aside></div>
      <footer class="vs-panel vs-footer"><span class="vs-kicker">${esc(pipeline.title || '未命名剧集')} · ${scripts.length} 个镜头脚本</span><div class="vs-actions"><button class="btn" type="button" data-goto-step="video">上一步：分镜脚本</button><button class="btn" type="button" data-save-video-script ${busy || !scripts.length ? 'disabled' : ''}>保存脚本</button>${stage.status === 'running' && scripts.length ? `<button class="btn primary" type="button" data-complete-video-script ${busy ? 'disabled' : ''}>保存视频脚本并完成阶段</button>` : ''}</div></footer>
    </section>`;
  }

  function safeVideoScriptUrl(value) {
    if (!value) return '';
    try {
      const url = new URL(value, location.origin);
      return url.origin === location.origin && /^\/api\/video-tasks\/[^/]+\/content$/.test(url.pathname) ? url.href : '';
    } catch (_) { return ''; }
  }

  function safeVideoScriptDownloadUrl(value) {
    const safeUrl = safeVideoScriptUrl(value);
    if (!safeUrl) return '';
    const url = new URL(safeUrl, location.origin);
    url.searchParams.set('download', '1');
    return url.href;
  }

  function pipelineMarkup(pipeline) {
    const data = localData(pipeline);
    const project = state.projects.find(item => item.id === state.projectId);
    const projectName = project ? (project.name || project.id) : '';
    const epIndex = state.pipelines.findIndex(item => item.pipeline_id === pipeline.pipeline_id);
    const epLabel = 'EP' + String((epIndex >= 0 ? epIndex : 0) + 1).padStart(2, '0');
    const stages = STAGES.map(spec => stageFor(pipeline, spec.key));
    const activeIndex = Math.max(0, STAGES.findIndex(item => item.key === state.activeStep));
    const stageViews = [scriptMarkup, assetsMarkup, storyboardMarkup, videoScriptMarkup];
    const progress = Math.round(Number(pipeline.progress?.percent || 0));

    if (state.activeStep === 'script') {
      return stageViews[0](pipeline, stages[0], data);
    }
    if (state.activeStep === 'assets') {
      return stageViews[1](pipeline, stages[1], data);
    }
    if (state.activeStep === 'video') {
      return stageViews[2](pipeline, stages[2], data);
    }
    if (state.activeStep === 'audio_compose') {
      return stageViews[3](pipeline, stages[3], data);
    }

    return `<section class="panel pipeline-shell"><div class="pipeline-title-row"><div class="pipeline-title-side"><h2 class="pipeline-page-title" title="EPISODE PIPELINE / ${esc(pipeline.pipeline_id)}"><span class="accent">剧集制片</span> / ${esc(projectName || '未选择项目')}</h2><div class="pipeline-status-side">${statusChip(pipeline.status)}<span>整体进度 ${progress}%</span></div></div><div><h2 class="pipeline-episode-title">${epLabel}-${esc(pipeline.title || '未命名剧集')}</h2><p>${esc(pipeline.seed || '尚未填写创作种子')}</p></div><div class="pipeline-meta-side"><button class="btn primary" type="button" data-open-create${state.projectId ? '' : ' disabled'}>新建剧集</button><select data-pipeline-select aria-label="选择剧集">${state.pipelines.map(item => `<option value="${esc(item.pipeline_id)}"${item.pipeline_id === pipeline.pipeline_id ? ' selected' : ''}>${esc(item.title || item.pipeline_id)}</option>`).join('')}</select></div></div><div class="pipeline-progress"><span style="width:${Math.max(0, Math.min(100, progress))}%"></span></div><div class="stage-stack">${stageViews[activeIndex](pipeline, stages[activeIndex], data)}</div></section>`;
  }

  function stageSectionId(key) { return key === 'video' ? 'stage-storyboard' : key === 'audio_compose' ? 'stage-video-script' : `stage-${key}`; }

  function renderStepNav() {
    const nav = q('#episodeStepNav');
    if (!nav) return;
    if (STAGES.some(spec => spec.key === state.activeStep)) {
      nav.hidden = true;
      nav.innerHTML = '';
      if (window.EpisodeLiquidMetal) window.EpisodeLiquidMetal.refresh();
      return;
    }
    const pipeline = currentPipeline();
    if (!pipeline) { nav.hidden = true; nav.innerHTML = ''; if (window.EpisodeLiquidMetal) window.EpisodeLiquidMetal.refresh(); return; }
    const previousFocus = document.activeElement?.closest?.('#episodeStepNav') ? String(document.activeElement.dataset.step || '') : '';
    const stages = STAGES.map(spec => stageFor(pipeline, spec.key));
    nav.hidden = false;
    nav.innerHTML = STAGES.map((spec, index) => `<button type="button" class="workflow-step ${state.activeStep === spec.key ? 'is-active' : ''} ${statusClass(stages[index].status)}" data-step="${esc(spec.key)}" role="tab" aria-selected="${state.activeStep === spec.key}" aria-controls="${stageSectionId(spec.key)}" tabindex="${state.activeStep === spec.key ? '0' : '-1'}"><strong>${esc(spec.display)}</strong><small>${esc(statusText(stages[index].status))}</small></button>`).join('');
    if (previousFocus) nav.querySelector(`[data-step="${CSS.escape(previousFocus)}"]`)?.focus();
    if (window.EpisodeLiquidMetal) window.EpisodeLiquidMetal.refresh();
  }

  function emptyPipelineMarkup() {
    const proj = state.projects.find(p => p.id === state.projectId);
    const projectName = proj ? (proj.name || proj.title || state.projectId) : (localStorage.getItem('workspace_project_name') || state.projectId);
    const libraries = state.libraries || [];
    const libraryOpts = libraries.map(lib => `<option value="${esc(lib.id)}">${esc(lib.name || lib.id)}</option>`).join('') || '<option value="episode">系统剧集提示词库</option>';

    return `<section class="panel empty-pipeline-shell champagne-card rounded-xl p-8 max-w-3xl mx-auto my-10 flex flex-col gap-6 shadow-2xl border border-gold-primary/20 bg-gradient-to-b from-[#14161f] to-[#0c0d12]">
      <div class="flex items-center justify-between pb-4 border-b border-white/10">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-lg bg-gold-primary/10 border border-gold-primary/30 flex items-center justify-center text-gold-light shadow-[0_0_12px_rgba(223,195,132,0.2)]">
            ${iconSvg('theater_comedy', 'w-6 h-6 text-gold-primary')}
          </div>
          <div>
            <div class="flex items-center gap-2">
              <span class="font-mono gw-type-body-smst text-gold-dim font-bold">PROJECT WORKBENCH</span>
              <span class="text-neutral-600 font-mono">/</span>
              <span class="font-display gw-type-body-md text-gold-light font-bold">${esc(projectName || '未命名项目')}</span>
            </div>
            <h2 class="font-display gw-type-headline-sm text-neutral-100 font-medium mt-0.5">从创建剧集（或者影片）开始</h2>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="px-2.5 py-1 rounded bg-[#090a0d] font-mono gw-type-body-sm text-neutral-400 border border-white/5">EMPTY PIPELINE</span>
        </div>
      </div>

      <p class="font-body gw-type-body-md text-neutral-300 leading-relaxed">
        当前项目尚未建立剧集或影片流水线。创建首部剧集后，剧本生成、资产提取、分镜设计与视频脚本将共享整套持久化任务上下文与工程资产。
      </p>

      <form data-create-form class="flex flex-col gap-4 bg-[#090a0d]/80 p-5 rounded-lg border border-white/5">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="flex flex-col gap-1.5">
            <label class="font-mono gw-type-body-md text-neutral-300r flex items-center justify-between">
              <span>剧集或影片名称 *</span>
              <span class="text-gold-dim font-normal">TITLE</span>
            </label>
            <input name="title" required maxlength="200" value="第一集" placeholder="例如：第一集 / 序幕 / 宣传短片" class="w-full bg-[#12141a] text-neutral-200 font-mono gw-type-body-md px-3.5 py-2 rounded border border-white/10 focus:outline-none focus:border-gold-primary/60 transition-all shadow-inner">
          </div>

          <div class="flex flex-col gap-1.5">
            <label class="font-mono gw-type-body-md text-neutral-300r flex items-center justify-between">
              <span>绑定提示词库</span>
              <span class="text-gold-dim font-normal">PROMPT LIBRARY</span>
            </label>
            <div class="relative">
              <select name="prompt_library_id" required class="w-full appearance-none bg-[#12141a] text-neutral-200 font-mono gw-type-body-md px-3.5 py-2 rounded border border-white/10 focus:outline-none focus:border-gold-primary/60 transition-all cursor-pointer">
                ${libraryOpts}
              </select>
              ${iconSvg('unfold_more', 'w-4 h-4 absolute right-3 top-2.5 text-neutral-400 pointer-events-none')}
            </div>
          </div>
        </div>

        <div class="flex flex-col gap-1.5">
          <label class="font-mono gw-type-body-md text-neutral-300r flex items-center justify-between">
            <span>创作种子 / 故事大纲</span>
            <span class="text-neutral-500 font-normal">SEED</span>
          </label>
          <textarea name="seed" maxlength="20000" rows="3" placeholder="在此输入本集或影片的故事大纲、核心冲突与主线线索（可选）..." class="w-full bg-[#12141a] text-neutral-200 font-body gw-type-body-md p-3 rounded border border-white/10 focus:outline-none focus:border-gold-primary/60 transition-all resize-none shadow-inner"></textarea>
        </div>

        <div class="flex items-center justify-between pt-2 border-t border-white/5 mt-1">
          <span class="form-error text-brand-red font-mono gw-type-body-sm" data-error role="alert"></span>
          <div class="flex items-center gap-3">
            <button class="btn-stitch-primary flex items-center gap-2 py-2 px-5 rounded bg-gradient-to-b from-[#e5ca8f] to-[#bfa15d] text-[#12141a] font-display gw-type-body-md font-boldr hover:brightness-110 shadow-[0_4px_16px_rgba(223,195,132,0.3)] active:translate-y-px transition-all cursor-pointer" type="submit">
              ${iconSvg('play_arrow', 'w-4 h-4')}
              <span>创建剧集流水线并进入工坊</span>
            </button>
          </div>
        </div>
      </form>
    </section>`;
  }

  function render() {
    const root = q('#episodePipeline');
    renderStepNav();
    if (state.activeStep === 'script') {
      document.body.classList.add('is-script-step');
    } else {
      document.body.classList.remove('is-script-step');
    }
    if (state.activeStep === 'assets') {
      document.body.classList.add('is-assets-step');
    } else {
      document.body.classList.remove('is-assets-step');
    }
    if (!root) return;
    if (!state.projectId) { root.innerHTML = '<section class="panel episode-empty"><h2>先选择一个项目</h2><p>剧集流水线必须绑定项目，项目权限会在每次读取和写入时校验。</p></section>'; return; }
    if (!state.pipelines.length) { root.innerHTML = emptyPipelineMarkup(); return; }
    root.innerHTML = pipelineMarkup(currentPipeline());
    if (state.activeStep === 'assets') {
      bindAssetTrackSync();
    }
    resumeVideoPolls(currentPipeline());
  }
  // 内置示例目录：**只在**真实 /api/asset-registry/projects 不可用时作为显式降级占位，
  // 必须在 UI 上标明「未接入」，不得被读成真实项目数据。
  const DEMO_PROJECTS_FALLBACK = [
    { id: 'proj-01', name: '《神谕之地》', title: '《神谕之地》' },
    { id: 'proj-02', name: '《赛博修真：重构法则》', title: '《赛博修真：重构法则》' },
    { id: 'proj-03', name: '《钛金纪元：序幕》', title: '《钛金纪元：序幕》' },
    { id: 'proj-05', name: '《霓虹脉冲 2099》', title: '《霓虹脉冲 2099》' }
  ];

  // 同上：该演示流水线不存在于任何后端，不可被当作真实任务快照。
  const DEMO_PIPELINE_FALLBACK = {
    pipeline_id: 'pipe-demo-01',
    project_id: 'proj-01',
    title: '第一集：深空信标',
    seed: '末日废土与神谕AI冲突，主角Ellen探索古代泰坦残骸，战术目镜与神经芯片接口连接。',
    status: 'running',
    progress: { percent: 65 },
    prompt_library_id: 'episode',
    stages: [
      { stage: 'script', label: '剧本生成', status: 'succeeded', result: { output_text: '### 《神谕之地》EP01：深空信标\n\n**场景一：暴雨天台**\n霓虹闪烁的废墟都市，暴雨倾泻。艾伦立于悬崖边缘，战术目镜扫描着远方的泰坦残骸...\n\n**场景二：神经芯片实验室**\n探针闪烁微弱琥珀光芒，生物芯片接入接口...' } },
      { stage: 'assets', label: '资产生成', status: 'succeeded', result: {} },
      { stage: 'video', label: '分镜脚本', status: 'running', result: {} },
      { stage: 'audio_compose', label: '视频脚本', status: 'queued', result: {} }
    ]
  };

  // 显式降级（用户 2026-09-21 裁决第 3 项 / Phase 9T）：本页原有的 `.catch(() => fallback)`
  // 是**静默降级**——后端未接入（404/501）时静默回落到内置演示数据，UI 上不说明来源，
  // 容易被读成真实数据。此处改为：仍保留可用回落（不破坏页面可用性），但把「未接入」
  // 写进页面可见的降级清单，并打 data-gw-degradation="not_integrated" 标记。
  const episodeDegradations = [];
  function recordDegradation(url, code) {
    const kind = code === 'NOT_INTEGRATED' ? 'not_integrated'
      : (code === 'SERVICE_UNAVAILABLE' ? 'service_unavailable' : 'error');
    if (!episodeDegradations.some(item => item.url === url && item.kind === kind)) {
      episodeDegradations.push({ url: String(url), kind });
    }
    const host = q('#episodeDegradation');
    if (!host) return;
    host.hidden = false;
    // 按 kind 归并，避免同一条文案重复堆叠；端点明细放进 title 便于核对。
    const groups = new Map();
    episodeDegradations.forEach(item => {
      const g = groups.get(item.kind) || { kind: item.kind, urls: [] };
      g.urls.push(item.url);
      groups.set(item.kind, g);
    });
    // 宿主元素上的单一标记按优先级汇总（未接入 > 服务不可用 > 其他），
    // 不使用「最后一次写入获胜」，否则同一页多个端点失败时标记不确定（审核发现 D2 同类问题）。
    host.dataset.gwDegradation = episodeDegradations.some(i => i.kind === 'not_integrated') ? 'not_integrated'
      : (episodeDegradations.some(i => i.kind === 'service_unavailable') ? 'service_unavailable' : 'error');
    host.innerHTML = [...groups.values()].map(group => {
      const text = group.kind === 'not_integrated' ? '该功能尚未接入后端（未纳入当前切片）'
        : (group.kind === 'service_unavailable' ? '后端服务暂时不可用，请稍后重试' : '后端请求失败');
      const suffix = group.urls.length > 1 ? ' ×' + group.urls.length : '';
      return `<span data-gw-degradation="${group.kind}" title="${esc(group.urls.join('\n'))}">${esc(text + suffix)}</span>`;
    }).join('');
    if (window.lucide?.createIcons) window.lucide.createIcons({attrs: {'aria-hidden': 'true'}});
  }
  // 重置本轮降级状态（审核发现 D1）：由 load() 在每次重新读取前调用。
  function resetDegradations() {
    episodeDegradations.length = 0;
    const host = q('#episodeDegradation');
    if (!host) return;
    host.hidden = true;
    host.removeAttribute('data-gw-degradation');
    host.textContent = '';
  }
  async function loadWithExplicitFallback(url, fallback) {
    try {
      return await api(url);
    } catch (error) {
      recordDegradation(url, error && error.code);
      return fallback;
    }
  }

  async function load() {
    state.busy = true;
    // 每次重新读取都清空上一轮的降级清单：否则端点接入后用户点「重新读取」，
    // 陈旧横幅仍会显示「该功能尚未接入后端」，与真实状态相反地误导（审核发现 D1）。
    resetDegradations();
    q('#episodePipeline').innerHTML = '<div class="episode-loading panel">正在读取项目、提示词和模型配置…</div>';
    try {
      const [projects, libraries, providers, videoIdentity] = await Promise.all([
        loadWithExplicitFallback('/api/asset-registry/projects?archived=false', { projects: DEMO_PROJECTS_FALLBACK }),
        loadWithExplicitFallback('/api/prompt-libraries', { libraries: [{ id: 'episode', name: '系统剧集提示词库', items: [] }] }),
        loadWithExplicitFallback('/api/providers', { providers: [] }),
        api('/api/asset-auth/identity-binding').catch(() => null),
      ]);
      state.videoActorId = videoIdentity?.bound === true && typeof videoIdentity.user_id === 'string' ? videoIdentity.user_id : '';
      // 契约对齐：项目中心 API 的稳定实体 ID 字段为 project_id（见 docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml，
// 黄金夹具 docs/fixtures/projects-hub-list-active.json 不含 id 字段）；此处归一化为内部 id，
// 否则项目下拉与项目名回退全部失效。
state.projects = (projects.projects || DEMO_PROJECTS_FALLBACK)
  .map(item => ({ ...item, id: item.id || item.project_id }));
      const libraryPayload = libraries.library && typeof libraries.library === 'object' ? libraries.library : libraries;
      state.libraries = Array.isArray(libraryPayload.libraries) ? libraryPayload.libraries : [{ id: 'episode', name: '系统剧集提示词库', items: [] }];
      state.providers = Array.isArray(providers.providers) ? providers.providers : [];
      if (state.projectId && !state.projects.some(item => item.id === state.projectId)) {
        // 尝试单项目查询，若存在则加入 state.projects；绝不强制重置为 proj-01
        try {
          // 只请求冻结的项目详情 GET；不得先向仅支持写操作的注册表单项路径发送请求。
          let singleP = null;
          try {
            singleP = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
          } catch (detailError) {
            // 保留失败显式登记，不把未知项目或服务错误静默吞掉。
            recordDegradation(`/api/projects/${encodeURIComponent(state.projectId)}`, detailError && detailError.code);
          }
          const pObj = singleP?.project || singleP;
          const pObjId = pObj && (pObj.id || pObj.project_id);
          if (pObjId) {
            state.projects.unshift({ id: pObjId, name: pObj.name || pObjId, title: pObj.name || pObjId });
          } else {
            const cachedName = localStorage.getItem('workspace_project_name') || state.projectId;
            state.projects.unshift({ id: state.projectId, name: cachedName, title: cachedName });
          }
        } catch (_) {
          const cachedName = localStorage.getItem('workspace_project_name') || state.projectId;
          state.projects.unshift({ id: state.projectId, name: cachedName, title: cachedName });
        }
      } else if (!state.projectId) {
        state.projectId = state.projects[0]?.id || 'proj-01';
      }
      libraryOptions();
      await loadPipelines();
    } catch (error) {
      console.warn('读取远端剧集流水线失败:', error);
      state.projects = DEMO_PROJECTS_FALLBACK;
      state.projectId = state.projectId || 'proj-01';
      state.libraries = [{ id: 'episode', name: '系统剧集提示词库', items: [] }];
      state.pipelines = [];
      state.selected = null;
      state.selectedPipelineId = '';
      render();
    } finally {
      state.busy = false;
    }
  }

  function createDefaultPipelineForProject(projectId) {
    const proj = state.projects.find(p => p.id === projectId);
    const projName = proj ? (proj.name || proj.title || projectId) : (localStorage.getItem('workspace_project_name') || projectId);
    if (projectId === 'proj-01') {
      return { ...DEMO_PIPELINE_FALLBACK, project_id: 'proj-01' };
    }
    return {
      pipeline_id: `pipe-init-${projectId}`,
      project_id: projectId,
      title: '第一集',
      seed: `${projName} 故事大纲与第一集创作线索。`,
      status: 'queued',
      progress: { percent: 0 },
      prompt_library_id: 'episode',
      stages: [
        { stage: 'script', label: '剧本生成', status: 'queued', result: { output_text: `### ${projName} EP01：第一集\n\n**场次一：序幕**\n围绕项目设定展开叙事线索与人物出场...\n` } },
        { stage: 'assets', label: '资产生成', status: 'queued', result: {} },
        { stage: 'video', label: '分镜脚本', status: 'queued', result: {} },
        { stage: 'audio_compose', label: '视频脚本', status: 'queued', result: {} }
      ]
    };
  }

  function clearAssetPoll(pipelineId, abort = false) {
    const run = state.assetRuns[pipelineId];
    if (!run) return;
    if (run.timer) window.clearTimeout(run.timer);
    if (run.autoStopTimer) window.clearTimeout(run.autoStopTimer);
    if (abort && run.controller) run.controller.abort();
    delete state.assetRuns[pipelineId];
  }
  function stageTimestamp(stage) {
    const value = stage?.updated_at || stage?.created_at;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) return numeric < 1e12 ? numeric * 1000 : numeric;
    const parsed = Date.parse(String(value || ''));
    return Number.isFinite(parsed) ? parsed : Date.now();
  }
  function ensureAssetRun(pipeline, startedAt = Date.now()) {
    if (!pipeline) return null;
    const existing = state.assetRuns[pipeline.pipeline_id];
    if (existing) return existing;
    const run = {pipelineId: pipeline.pipeline_id, startedAt, timer: 0, autoStopTimer: 0, controller: new AbortController(), stopRequested: false, timeoutNotified: false};
    const remaining = Math.max(0, ASSET_AUTO_STOP_MS - (Date.now() - startedAt));
    run.autoStopTimer = window.setTimeout(() => {
      if (!run.stopRequested && state.assetRuns[pipeline.pipeline_id] === run) {
        run.timeoutNotified = true;
        stopStage(pipeline, 'assets', true).catch(error => toast(error.message || '自动停止失败', true));
      }
    }, remaining);
    state.assetRuns[pipeline.pipeline_id] = run;
    return run;
  }
  function scheduleAssetPoll(pipelineId, delay = ASSET_POLL_INTERVAL_MS) {
    const run = state.assetRuns[pipelineId];
    if (!run || run.timer) return;
    run.timer = window.setTimeout(() => {
      run.timer = 0;
      pollAssetStage(pipelineId);
    }, delay);
  }
  function syncAssetPolling(pipeline) {
    const stage = stageFor(pipeline, 'assets');
    if (!pipeline) return;
    if (['running', 'cancel_requested', 'recovering'].includes(stage.status)) {
      const run = ensureAssetRun(pipeline, stageTimestamp(stage));
      if (run && Date.now() - run.startedAt >= ASSET_AUTO_STOP_MS && !run.stopRequested) {
        run.timeoutNotified = true;
        stopStage(pipeline, 'assets', true).catch(error => toast(error.message || '自动停止失败', true));
        return;
      }
      scheduleAssetPoll(pipeline.pipeline_id);
    } else clearAssetPoll(pipeline.pipeline_id);
  }
  function syncAllAssetPolling() { state.pipelines.forEach(syncAssetPolling); }
  function replacePipeline(snapshot) {
    if (!snapshot?.pipeline_id) return;
    const index = state.pipelines.findIndex(item => item.pipeline_id === snapshot.pipeline_id);
    if (index >= 0) state.pipelines[index] = snapshot;
    else state.pipelines.push(snapshot);
    if (state.selected?.pipeline_id === snapshot.pipeline_id || state.selectedPipelineId === snapshot.pipeline_id) state.selected = snapshot;
  }
  async function pollAssetStage(pipelineId) {
    const run = state.assetRuns[pipelineId];
    if (!run || run.stopRequested) return;
    const pipeline = state.pipelines.find(item => item.pipeline_id === pipelineId);
    if (!pipeline) return clearAssetPoll(pipelineId);
    if (Date.now() - run.startedAt >= ASSET_AUTO_STOP_MS) {
      run.timeoutNotified = true;
      return stopStage(pipeline, 'assets', true);
    }
    try {
      const payload = await api(`/api/episode-pipelines/${encodeURIComponent(pipelineId)}`);
      if (state.assetRuns[pipelineId] !== run || run.stopRequested) return;
      const snapshot = payload.pipeline || payload;
      replacePipeline(snapshot);
      const stage = stageFor(snapshot, 'assets');
      render();
      if (['running', 'cancel_requested', 'recovering'].includes(stage.status)) scheduleAssetPoll(pipelineId);
      else {
        clearAssetPoll(pipelineId);
        toast(`资产阶段${statusText(stage.status)}`);
      }
    } catch (error) {
      if (error?.name === 'AbortError') return;
      if (state.assetRuns[pipelineId] === run) {
        toast('资产状态读取暂时失败，继续重试', true);
        scheduleAssetPoll(pipelineId, ASSET_POLL_INTERVAL_MS * 2);
      }
    }
  }

  function isVirtualPipeline(pipeline) {
    if (!pipeline || !pipeline.pipeline_id) return true;
    const id = String(pipeline.pipeline_id);
    return id.startsWith('pipe-init-') || id === 'pipe-demo-01';
  }

  async function ensurePersistedPipeline(pipeline) {
    if (!isVirtualPipeline(pipeline)) return pipeline;
    const promptLibraryId = String(pipeline.prompt_library_id || 'episode');
    const promptItemIds = Object.fromEntries(STAGES.map(spec => [spec.key, spec.prompt]));
    const proj = state.projects.find(p => p.id === state.projectId);
    const projName = proj ? (proj.name || proj.title || state.projectId) : (localStorage.getItem('workspace_project_name') || state.projectId);
    const title = String(pipeline.title || '第一集').trim();
    const userDraft = String(q('[data-script-draft]')?.value || '').trim();
    const seed = userDraft || String(pipeline.seed || '').trim() || `${projName} 故事大纲与第一集创作线索。`;
    const data = await api('/api/episode-pipelines', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID ? crypto.randomUUID() : `episode-${Date.now()}`
      },
      body: JSON.stringify({
        project_id: state.projectId,
        title: title || '第一集',
        seed: seed,
        prompt_library_id: promptLibraryId,
        prompt_item_ids: promptItemIds
      })
    });
    const oldLocal = localData(pipeline);
    writeWorkspace(data, oldLocal);
    state.selected = data;
    state.selectedPipelineId = data.pipeline_id;
    await loadPipelines();
    return data;
  }

  async function loadPipelines() {
    if (!state.projectId) state.projectId = 'proj-01';
    // 显式降级（审核发现 D5 / 承接 D3）：此处原为 `try/catch` + `console.warn` 的**静默降级**，
    // 失败后把 incoming 置空，UI 会显示「尚未建立剧集或影片流水线」——读者会理解成
    // 「我没有流水线」，而不是「后端读取失败」。Phase 12 已真实接入
    // `/api/episode-pipelines`（GET/POST 实测 200 + data_status=ok），因此失败必须登记可见降级；
    // 回落值仍为空数组，可用性不变。
    const data = await loadWithExplicitFallback(`/api/episode-pipelines?project_id=${encodeURIComponent(state.projectId)}`, { pipelines: [] });
    const incoming = data.pipelines || [];
    const activeIds = new Set(incoming.map(item => item.pipeline_id));
    Object.keys(state.assetRuns).forEach(id => {
      if (!activeIds.has(id)) clearAssetPoll(id, true);
    });
    state.pipelines = incoming;
    state.selected = state.pipelines.find(item => item.pipeline_id === state.selectedPipelineId) || state.pipelines.find(item => item.pipeline_id === state.selected?.pipeline_id) || state.pipelines[0] || null;
    state.selectedPipelineId = state.selected?.pipeline_id || '';
    syncAuraScope(state.selected);
    activateVideoContext(state.selected);
    state.pipelines.forEach(item => {
      const text = stageOutput(item, stageFor(item, 'script'));
      if (text && stageFor(item, 'script').status === 'succeeded') archiveScriptAsset(item, text);
    });
    render();
    syncAllAssetPolling();
  }
  function openCreate() { q('#createPanel').hidden = false; q('#createForm [name="title"]').focus(); }
  function closeCreate() { q('#createPanel').hidden = true; q('#createError').textContent = ''; }
  async function create(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    const errorEl = form.querySelector('[data-error]') || q('#createError');
    if (errorEl) errorEl.textContent = '';
    toast('正在执行：创建剧集流水线');
    try {
      const formData = new FormData(form);
      const promptLibraryId = String(formData.get('prompt_library_id') || 'episode');
      const promptItemIds = Object.fromEntries(STAGES.map(spec => [spec.key, spec.prompt]));
      const title = String(formData.get('title') || '').trim() || '第一集';
      const seed = String(formData.get('seed') || '').trim();
      const data = await api('/api/episode-pipelines', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': crypto.randomUUID ? crypto.randomUUID() : `episode-${Date.now()}`
        },
        body: JSON.stringify({
          project_id: state.projectId,
          title: title,
          seed: seed,
          prompt_library_id: promptLibraryId,
          prompt_item_ids: promptItemIds
        })
      });
      closeCreate();
      state.selected = data;
      state.selectedPipelineId = data.pipeline_id;
      await loadPipelines();
      toast('剧集流水线已创建');
    } catch (error) {
      if (errorEl) errorEl.textContent = error.message || '创建失败';
    } finally {
      if (submit) submit.disabled = false;
    }
  }
  function pipelineFor(id) { return state.pipelines.find(item => item.pipeline_id === id) || currentPipeline(); }
  async function startStage(stage, pipelineId, options = {}) { let pipeline = pipelineFor(pipelineId); if (!pipeline) throw new Error('未找到剧集流水线'); if (isVirtualPipeline(pipeline)) { pipeline = await ensurePersistedPipeline(pipeline); } const {onAccepted, ...requestOptions} = options; await api(`/api/episode-pipelines/${encodeURIComponent(pipeline.pipeline_id)}/stages/${encodeURIComponent(stage)}/start`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(requestOptions)}); if (typeof onAccepted === 'function') onAccepted(); state.activeStep = stage; syncStepParam(stage); await loadPipelines(); return currentPipeline(); }
  async function startStageIfNeeded(stage, pipeline, options = {}) { if (isVirtualPipeline(pipeline)) { pipeline = await ensurePersistedPipeline(pipeline); } const current = stageFor(pipeline, stage); if (current.status === 'queued' || ['succeeded', 'failed', 'canceled', 'interrupted'].includes(current.status)) { await startStage(stage, pipeline.pipeline_id, options); const next = currentPipeline(); if (stage === 'assets') ensureAssetRun(next); return next; } if (stage === 'assets' && ['running', 'cancel_requested', 'recovering'].includes(current.status)) ensureAssetRun(pipeline); return pipeline; }
  async function stopStage(pipeline, stageKey, automatic = false) { const target = pipelineFor(pipeline?.pipeline_id); if (!target) throw new Error('未找到剧集流水线'); const stage = stageFor(target, stageKey); const run = ensureAssetRun(target); if (run?.stopRequested) return; if (run) { run.stopRequested = true; if (run.timer) window.clearTimeout(run.timer); if (run.autoStopTimer) window.clearTimeout(run.autoStopTimer); if (run.controller) run.controller.abort(); } toast(automatic ? '资产生成超过 2 分钟，正在自动停止' : '正在停止资产生成，等待后台确认'); try { const result = await api(`/api/episode-pipelines/${encodeURIComponent(target.pipeline_id)}/stages/${encodeURIComponent(stageKey)}/cancel`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_version: stage.version || undefined, message: automatic ? '资产生成超时自动停止' : '用户手动停止资产生成'})}); await loadPipelines(); const finalStatus = String(result.stage?.status || stageFor(currentPipeline(), stageKey).status || ''); toast(finalStatus === 'succeeded' ? '资产生成已完成，无需停止' : automatic ? '资产生成已自动停止，可重新执行' : '资产生成已停止，可重新执行'); } finally { clearAssetPoll(target.pipeline_id); } }
  async function completeStage(stage, pipelineId, payload = {}) { const pipeline = pipelineFor(pipelineId); if (!pipeline) throw new Error('未找到剧集流水线'); await api(`/api/episode-pipelines/${encodeURIComponent(pipeline.pipeline_id)}/stages/${encodeURIComponent(stage)}/complete`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status: payload.status || 'succeeded', output_asset_ids: payload.assets || [], output_text: String(payload.text || ''), source_job_id: String(payload.sourceJobId || ''), message: String(payload.message || '')})}); await loadPipelines(); }
  function updateInputData(pipeline) { const data = localData(pipeline); const draft = q('[data-script-draft]'); if (draft) data.scriptDraft = draft.value; const output = q('[data-script-output]'); if (output) data.scriptOutput = output.value; const mode = q('[data-script-mode]'); if (mode && SCRIPT_MODES.some(item => item.key === mode.value)) data.scriptMode = mode.value; const genre = q('[data-script-genre]'); if (genre) data.scriptGenre = genre.value; const style = q('[data-script-style]'); if (style) data.scriptStyle = style.value; const duration = q('[data-script-duration]'); if (duration) data.scriptDuration = duration.value; const pacing = q('[data-script-pacing]'); if (pacing) data.scriptPacing = SCRIPT_PACINGS[Number(pacing.value)] || data.scriptPacing; const outline = q('[data-outline]'); if (outline) data.storyboard.outline = outline.value; const outlineFieldsPresent = document.querySelector('[data-outline-field]') || document.querySelector('[data-outline-act]'); if (!outline && outlineFieldsPresent) { const current = outlineFields(data.storyboard.outline); document.querySelectorAll('[data-outline-field]').forEach(input => { if (Object.prototype.hasOwnProperty.call(current, input.dataset.outlineField)) current[input.dataset.outlineField] = input.value; }); document.querySelectorAll('[data-outline-act]').forEach(input => { const index = Number(input.dataset.outlineAct); if (Number.isInteger(index) && index >= 0 && index < current.fiveActs.length) current.fiveActs[index] = input.value; }); data.storyboard.outline = JSON.stringify(current, null, 2); } document.querySelectorAll('[data-shot-field]').forEach(input => { const shot = data.storyboard.shots.find(item => item.id === input.dataset.shotId); if (shot) shot[input.dataset.shotField] = input.value; }); document.querySelectorAll('[data-video-field]').forEach(input => { const item = data.videoScripts.find(value => value.id === input.dataset.videoId); if (item) item[input.dataset.videoField] = input.value; }); writeWorkspace(pipeline, data); return data; }
  function requestPayload(pipeline, stageKey, itemId, message, kindOverride, generationContext = null, systemPromptOverride = '') { const selection = selectedProviderModel(pipeline, stageKey, kindOverride); if (!selection.provider_id || !selection.model) throw new Error('请先在当前阶段选择已配置模型'); const documentPrompt = promptDocument(pipeline, itemId); const context = contextParams(pipeline, stageFor(pipeline, stageKey)); const isScriptAgent = stageKey === 'script'; const payload = {conversation_id: '', message: promptMessage(documentPrompt, message), system_prompt: systemPromptOverride || `${documentPrompt.positive}\n\n${documentPrompt.negative ? `负面约束：${documentPrompt.negative}` : ''}`, mode: 'agent', model: selection.model, ms_model: selection.provider_id === 'modelscope' ? selection.model : '', provider: selection.provider_id, image_model: '', image_provider: '', prompt_library_id: context.episode_prompt_library_id, prompt_item_id: itemId, production_context: context, agent_id: isScriptAgent ? SCRIPT_ARCHITECT_AGENT_ID : '', agent_route: isScriptAgent ? SCRIPT_ARCHITECT_ROUTE : ''}; if (generationContext && typeof generationContext === 'object') payload.generation_context = generationContext; return payload; }
  function responseText(response) { const content = response.message?.content ?? response.message?.text ?? response.content ?? ''; const text = typeof content === 'object' ? JSON.stringify(content) : String(content || '').trim(); if (!text) throw new Error('模型返回空结果'); return text; }
  async function callTextAgent(pipeline, stageKey, itemId, message, kindOverride = 'text', signal) {
    const payload = requestPayload(pipeline, stageKey, itemId, message, kindOverride);
    const operation = auraOperation(stageKey, itemId);
    auraTrace(stageKey, 'INPUT', `已接收：${operation}`, {itemId, provider: payload.provider, model: payload.model}, operation, 'running', pipeline);
    auraTrace(stageKey, 'THINK', `已路由至 ${auraAgentFor(stageKey)}，等待模型返回`, {route: '/api/chat/agent', prompt_item_id: itemId}, operation, 'running', pipeline);
    try {
      const response = await api('/api/chat/agent', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal});
      const text = responseText(response);
      auraTrace(stageKey, 'SYNTH', auraResultSummary(text), {content_chars: text.length, content_checksum: checksum(text)}, operation, 'succeeded', pipeline);
      return text;
    } catch (error) {
      auraTrace(stageKey, 'EXEC', `执行失败（${auraErrorType(error)}）`, {route: '/api/chat/agent', error_type: auraErrorType(error)}, operation, 'failed', pipeline);
      throw error;
    }
  }
  async function callTextModel(pipeline, stageKey, itemId, message, generationContext, signal, systemPromptOverride = '') {
    const payload = requestPayload(pipeline, stageKey, itemId, message, 'text', generationContext, systemPromptOverride);
    payload.mode = 'chat';
    const operation = auraOperation(stageKey, itemId);
    auraTrace(stageKey, 'INPUT', `已接收：${operation}`, {generation_context: payload.generation_context || null, itemId, provider: payload.provider, model: payload.model}, operation, 'running', pipeline);
    auraTrace(stageKey, 'THINK', `已路由至 ${auraAgentFor(stageKey)}，等待模型返回`, {route: '/api/chat', agent_id: payload.agent_id || '', agent_route: payload.agent_route || '', prompt_item_id: itemId, mode: payload.mode}, operation, 'running', pipeline);
    try {
      const response = await api('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal});
      const text = responseText(response);
      auraTrace(stageKey, 'SYNTH', auraResultSummary(text), {content_chars: text.length, content_checksum: checksum(text)}, operation, 'succeeded', pipeline);
      return text;
    } catch (error) {
      auraTrace(stageKey, 'EXEC', `执行失败（${auraErrorType(error)}）`, {route: '/api/chat', error_type: auraErrorType(error)}, operation, 'failed', pipeline);
      throw error;
    }
  }
  function reviewList(value) { const values = Array.isArray(value) ? value : String(value || '').split(/\n|\r|；|;/).map(item => item.replace(/^\s*[-*•●]\s*/, '').trim()).filter(Boolean); return values.map(item => typeof item === 'object' ? String(item.text || item.content || item.message || item.note || JSON.stringify(item)) : String(item)).filter(Boolean); }
  function strictReviewTextArray(value, field) { if (!Array.isArray(value) || !value.length || value.some(item => typeof item !== 'string' || !item.trim())) throw new Error(`剧本审核返回格式不正确：${field} 必须是非空字符串数组`); return value.map(item => item.trim()); }
  function strictReviewScore(value, field) { if (typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value > 100) throw new Error(`剧本审核返回格式不正确：${field} 必须是 0-100 的整数`); return value; }
  function strictStoryboardTextArray(value, field, min, max) { if (!Array.isArray(value) || value.length < min || value.length > max || value.some(item => typeof item !== 'string' || !item.trim() || !/[\u4e00-\u9fff]/.test(item))) throw new Error(`分镜审核返回格式不正确：${field} 必须是 ${min}-${max} 条中文字符串数组`); return value.map(item => item.trim()); }
  function normalizeScriptReview(text) {
    const raw = String(text || '').trim();
    if (!raw || /^```/.test(raw) || /```$/.test(raw) || raw.includes('```')) throw new Error('剧本审核返回格式不正确：只能返回裸 JSON，不能使用 Markdown 代码块');
    let parsed;
    try { parsed = JSON.parse(raw); } catch (_) { throw new Error('剧本审核返回格式不正确：返回内容不是合法 JSON'); }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('剧本审核返回格式不正确：顶层必须是 JSON 对象');
    const expectedKeys = SCRIPT_REVIEW_KEYS.slice().sort().join('|');
    if (Object.keys(parsed).sort().join('|') !== expectedKeys) throw new Error(`剧本审核返回格式不正确：顶层字段必须严格为 ${SCRIPT_REVIEW_KEYS.join('、')}`);
    const score = strictReviewScore(parsed.score, 'score');
    if (parsed.status !== 'passed' && parsed.status !== 'failed') throw new Error('剧本审核返回格式不正确：status 只能是 passed 或 failed');
    const verdictPrefix = parsed.status === 'passed' ? '审核通过' : '审核未通过';
    if (typeof parsed.overallVerdict !== 'string' || !parsed.overallVerdict.trim().startsWith(verdictPrefix)) throw new Error(`剧本审核返回格式不正确：status 为 ${parsed.status} 时，overallVerdict 必须以“${verdictPrefix}”开头`);
    const problems = strictReviewTextArray(parsed.problems, 'problems');
    const suggestions = strictReviewTextArray(parsed.suggestions, 'suggestions');
    const priority = strictReviewTextArray(parsed.priority, 'priority');
    const revisionPath = strictReviewTextArray(parsed.revisionPath, 'revisionPath');
    if (typeof parsed.rewriteExample !== 'string' || !parsed.rewriteExample.trim()) throw new Error('剧本审核返回格式不正确：rewriteExample 必须是非空字符串');
    if (!Array.isArray(parsed.surgeryTable) || parsed.surgeryTable.length < 1 || parsed.surgeryTable.length > 3 || parsed.surgeryTable.some(item => !item || typeof item !== 'object' || Object.keys(item).sort().join('|') !== 'diagnosis|original|rewrite' || ['original', 'diagnosis', 'rewrite'].some(key => typeof item[key] !== 'string' || !item[key].trim()))) throw new Error('剧本审核返回格式不正确：surgeryTable 必须包含 1-3 条且每条严格包含 original、diagnosis、rewrite');
    if (!Array.isArray(parsed.dimensions) || parsed.dimensions.length !== SCRIPT_REVIEW_DIMENSIONS.length) throw new Error('剧本审核返回格式不正确：dimensions 必须正好包含 7 个维度');
    parsed.dimensions.forEach((item, index) => { if (!item || typeof item !== 'object' || Object.keys(item).sort().join('|') !== 'comment|name|score' || item.name !== SCRIPT_REVIEW_DIMENSIONS[index]) throw new Error(`剧本审核返回格式不正确：dimensions[${index}] 必须严格包含 name、score、comment，且 name 为 ${SCRIPT_REVIEW_DIMENSIONS[index]}`); strictReviewScore(item.score, `dimensions[${index}].score`); if (typeof item.comment !== 'string' || !item.comment.trim()) throw new Error(`剧本审核返回格式不正确：dimensions[${index}].comment 不能为空`); });
    return {score, status: parsed.status, overallVerdict: parsed.overallVerdict.trim(), problems, suggestions, priority, rewriteExample: parsed.rewriteExample.trim(), revisionPath, surgeryTable: parsed.surgeryTable.map(item => ({original: item.original.trim(), diagnosis: item.diagnosis.trim(), rewrite: item.rewrite.trim()})), dimensions: parsed.dimensions.map(item => ({name: item.name, score: item.score, comment: item.comment.trim()})), reviewer: '审核管 agent', raw, at: Date.now()};
  }
  function normalizeStoryboardReview(text) { const raw = String(text || '').trim(); if (!raw || raw.includes('```')) throw new Error('分镜审核返回格式不正确：只能返回裸 JSON，不能使用 Markdown 代码块'); let parsed; try { parsed = JSON.parse(raw); } catch (_) { throw new Error('分镜审核返回格式不正确：返回内容不是合法 JSON'); } if (!exactKeys(parsed, STORYBOARD_REVIEW_KEYS)) throw new Error(`分镜审核返回格式不正确：顶层字段必须严格为 ${STORYBOARD_REVIEW_KEYS.join('、')}`); const score = strictReviewScore(parsed.score, 'score'); const status = parsed.status === 'passed' || parsed.status === 'failed' ? parsed.status : ''; if (!status || (score >= 80 ? status !== 'passed' : status !== 'failed')) throw new Error('分镜审核返回格式不正确：status 必须与 80 分阈值一致'); if (typeof parsed.summary !== 'string' || !parsed.summary.trim() || !/[\u4e00-\u9fff]/.test(parsed.summary)) throw new Error('分镜审核返回格式不正确：summary 必须是非空中文审片结论'); const issues = strictStoryboardTextArray(parsed.issues, 'issues', 2, 100); const suggestions = strictStoryboardTextArray(parsed.suggestions, 'suggestions', 2, 100); const priority = strictStoryboardTextArray(parsed.priority, 'priority', 2, 5); if (!Array.isArray(parsed.dimensions) || parsed.dimensions.length !== STORYBOARD_REVIEW_DIMENSIONS.length) throw new Error('分镜审核返回格式不正确：dimensions 必须正好包含 5 个维度'); const dimensions = parsed.dimensions.map((item, index) => { if (!item || !exactKeys(item, ['key', 'score', 'comment']) || item.key !== STORYBOARD_REVIEW_DIMENSIONS[index]) throw new Error(`分镜审核返回格式不正确：dimensions[${index}] 必须严格对应 ${STORYBOARD_REVIEW_DIMENSIONS[index]}`); const dimensionScore = strictReviewScore(item.score, `dimensions[${index}].score`); if (typeof item.comment !== 'string' || !item.comment.trim() || !/[\u4e00-\u9fff]/.test(item.comment)) throw new Error(`分镜审核返回格式不正确：dimensions[${index}].comment 必须是非空中文简评`); return {key: item.key, name: item.key, score: dimensionScore, comment: item.comment.trim()}; }); return {score, status, summary: parsed.summary.trim(), overallVerdict: parsed.summary.trim(), issues, problems: issues, suggestions, priority, dimensions, revisionPath: [], rewriteExample: '', surgeryTable: [], reviewer: '严格分镜审片官', raw, at: Date.now()}; }
  function storyboardReviewFeedback(review) { const issues = reviewList(review?.issues || review?.problems); return {issues, suggestions: reviewList(review?.suggestions), priority: reviewList(review?.priority), dimensions: Array.isArray(review?.dimensions) ? review.dimensions.map(item => ({key: String(item.key || item.name || '审核维度'), score: Number(item.score || 0), comment: String(item.comment || '')})) : [], summary: String(review?.summary || review?.overallVerdict || '').trim()}; }
  function reviewRevisionText(review) { return JSON.stringify({overallVerdict: review?.overallVerdict || '', problems: review?.problems || [], suggestions: review?.suggestions || [], priority: review?.priority || [], revisionPath: review?.revisionPath || [], surgeryTable: review?.surgeryTable || [], dimensions: review?.dimensions || []}, null, 2); }
  function scriptReviewMode(data, review = null) { const latestVersion = Array.isArray(data?.history) ? data.history.find(item => ['script-generated', 'script-revised'].includes(item.type) && item.scriptMode) : null; const key = String(review?.scriptMode || latestVersion?.scriptMode || data?.scriptMode || 'planning'); return SCRIPT_MODES.find(item => item.key === key) || SCRIPT_MODES[0]; }
  function scriptReviewRules(mode) { return mode.key === 'planning'
    ? '这是“剧本规划稿”审核，不是成片剧本审核。不得因为缺少“场次标题+景别/动作+角色台词”的成片格式扣分；这些要求只适用于完整剧本正文。必须检查方向确认、故事核心、角色档案、关系矩阵、三幕结构、场次拆解、视觉符号与资产线索、写作防错八个规划章节及其字段完整性、因果链和可拍性。场次拆解应检查场景/时间、戏剧目标、可见冲突、情绪转折、进出场方式、连续性线索和视觉信息，但不强制虚构成片对白。FormatCompliance 只评价规划稿结构与字段是否符合当前规划提示词。'
    : mode.key === 'writing'
      ? '这是“完整剧本正文”审核。必须检查是否具备可直接拍摄的场次标题、时间/地点、景别或镜头动作、角色行为与角色台词，并结合单集时长、竖屏节奏和视觉连续性判断格式是否可执行。'
      : '这是“剧本优化重生”审核。按完整可拍剧本正文检查格式，同时检查是否保留原故事结构、角色动机和关键视觉信息，是否具体降 AI 味并增强动作、对白和节奏。'; }
  function scriptReviewSystemPrompt(pipeline, mode) { const documentPrompt = promptDocument(pipeline, 'episode_script_review'); return `${documentPrompt.positive}\n\n当前模式规则优先级高于通用成片审核规则：${scriptReviewRules(mode)}\n\n${documentPrompt.negative ? `负面约束：${documentPrompt.negative}` : ''}`; }
  function scriptRevisionSystemPrompt(pipeline, mode) { const documentPrompt = promptDocument(pipeline, mode.prompt); const rules = mode.key === 'planning'
    ? '这是规划稿重写。保持并补齐规划稿的八个章节及其字段，不要把它改成只含场次标题、景别、动作和对白的成片剧本；不得为了修复格式意见而丢失方向确认、关系矩阵、资产线索或写作防错。'
    : '这是成片剧本重写。按审核意见输出可直接拍摄的完整 Markdown 剧本，保留原故事核心与角色关系，补齐场次标题、时间地点、景别/镜头动作、角色行为和台词，确保符合单集时长与竖屏节奏。';
    return `${documentPrompt.positive}\n\n当前模式规则优先级高于通用剧本格式规则：${rules}\n\n${documentPrompt.negative ? `负面约束：${documentPrompt.negative}` : ''}`; }
  function scriptReviewInstruction(mode, text) {
    const rules = scriptReviewRules(mode);
    return `你是 Gods' Workbench 的剧本审核 agent。当前输出模式：${mode.label}（${mode.key}）。${rules}只返回当前剧集提示词库中的 episode_script_review 文档规定的一个合法 JSON 对象，禁止 Markdown 代码块、禁止任何额外字段或解释；必须逐项返回 score、status、overallVerdict、problems、suggestions、priority、rewriteExample、revisionPath、surgeryTable 和 7 个固定 dimensions。审核意见必须针对当前模式，不得把另一种模式的格式要求当作扣分依据。\n\n待审核内容：\n${text}`;
  }
  function scriptRevisionInstruction(mode, text, review, generationContext) {
    const settings = scriptGenerationSettingsText(generationContext);
    const rules = mode.key === 'planning'
      ? '这是规划稿重写。保持并补齐规划稿的八个章节及其字段，不要把它改成只含场次标题、景别、动作和对白的成片剧本；不得为了修复格式意见而丢失方向确认、关系矩阵、资产线索或写作防错。'
      : '这是成片剧本重写。按审核意见输出可直接拍摄的完整 Markdown 剧本，保留原故事核心与角色关系，补齐场次标题、时间地点、景别/镜头动作、角色行为和台词，确保符合单集时长与竖屏节奏。';
    return `${settings}\n\n你是剧本修改 agent。当前输出模式：${mode.label}（${mode.key}）。${rules}只输出修改后的 Markdown 正文，不要解释生成过程。\n\n原稿：\n${text}\n\n审核意见：\n${reviewRevisionText(review)}`;
  }
  async function generateScript(pipeline) {
    if (pendingFor(pipeline, 'scriptGeneration')) return;
    const data = updateInputData(pipeline);
    const source = String(data.scriptDraft || pipeline.seed || '');
    if (!source.trim()) throw new Error('请先输入剧情或创作种子');
    const mode = SCRIPT_MODES.find(item => item.key === data.scriptMode) || SCRIPT_MODES[0];
    const generationContext = scriptGenerationContext(data, mode);
    const settings = scriptGenerationSettingsText(generationContext);
    const message = mode.key === 'planning' ? `${settings}\n\n请按当前提示词文档，把以下剧情描述生成可供后续写作的剧本规划稿，只输出规划稿正文，不要解释生成过程。\n\n${source}` : mode.key === 'writing' ? `${settings}\n\n请按当前提示词文档，把以下输入写成可直接送审的完整中文剧本正文，只输出 Markdown 剧本，不要解释生成过程。\n\n${source}` : `${settings}\n\n请按当前提示词文档，对以下剧本做保结构、降 AI 味、增强可拍性的优化重生，只输出优化后的 Markdown 剧本正文，不要解释生成过程。\n\n${source}`;
    requestPayload(pipeline, 'script', mode.prompt, message, 'text', generationContext);
    const pendingPipeline = pipeline;
    setPending(pendingPipeline, 'scriptGeneration', true);
    render();
    let started = false;
    try {
      pipeline = await startStageIfNeeded('script', pipeline, {onAccepted: () => { started = true; }});
      setPending(pipeline, 'scriptGeneration', true);
      render();
      const text = await callTextModel(pipeline, 'script', mode.prompt, message, generationContext);
      const next = localData(pipeline);
      next.scriptMode = mode.key;
      next.scriptOutput = text;
      remember(pipeline, next, 'script-generated', {text: text.slice(0, 500), scriptMode: mode.key, promptItemId: mode.prompt});
      archiveScriptAsset(pipeline, text);
      await completeStage('script', pipeline.pipeline_id, {text, message: '剧本模型生成已完成'});
      toast('剧本已生成并自动保存，点击送审继续');
    } catch (error) {
      if (started) {
        try { await completeStage('script', pipeline.pipeline_id, {status: 'failed', message: '剧本生成失败，请重试'}); }
        catch (_) { /* Preserve the original error and any locally saved output. */ }
      }
      throw error;
    } finally {
      setPending(pendingPipeline, 'scriptGeneration', '');
      setPending(pipeline, 'scriptGeneration', '');
      render();
    }
  }
  async function saveScriptVersion(pipeline) { const data = updateInputData(pipeline); const text = String(q('[data-script-output]')?.value || '').trim(); if (!text) throw new Error('请先填写剧本正文'); data.scriptOutput = text; remember(pipeline, data, 'script-generated', {text: text.slice(0, 500)}); archiveScriptAsset(pipeline, text); render(); toast('当前剧本版本已保存并归档'); }
  async function sendReview(pipeline, kind = 'script') { const data = updateInputData(pipeline); const text = kind === 'script' ? (data.scriptOutput || stageResultText(stageFor(pipeline, 'script')) || data.scriptDraft) : JSON.stringify(data.storyboard.shots); if (!text.trim()) throw new Error('没有可审核的内容'); const itemId = kind === 'script' ? 'episode_script_review' : 'episode_prompt_segment_review'; const reviewer = kind === 'script' ? '审核管 agent' : '分镜审核官 agent'; const pendingKey = kind === 'script' ? 'scriptReview' : 'storyboardReview'; const mode = kind === 'script' ? scriptReviewMode(data) : null; const generationContext = mode ? scriptGenerationContext(data, mode) : null; setPending(pipeline, pendingKey, 'review'); render(); try { const instruction = kind === 'script' ? scriptReviewInstruction(mode, text) : `你是 Gods' Workbench 的严格分镜审片官。请严格按当前剧集提示词库中的 episode_prompt_segment_review 文档审核以下步骤2分镜，只返回一个合法 JSON 对象，不要 Markdown、不要解释、不要额外字段。顶层字段必须严格为 score、status、summary、issues、suggestions、priority、dimensions；score 为 0-100 整数，80 及以上 status 必须为 passed，否则必须为 failed；issues 至少 2 条，suggestions 至少 2 条，priority 必须按执行顺序返回 2-5 条；dimensions 必须按 NarrativeClarity、ShotContinuity、VisualExecutability、RhythmControl、AssetConsistency 顺序各返回 key、score、comment。每条意见必须具体到镜头和可改字段，并能直接用于按审核意见重拆分。\n\n${text}`; const raw = kind === 'script' ? await callTextModel(pipeline, 'script', itemId, instruction, generationContext, undefined, scriptReviewSystemPrompt(pipeline, mode)) : await callTextAgent(pipeline, 'video', itemId, instruction); const review = kind === 'script' ? {...normalizeScriptReview(raw), scriptMode: mode.key, generationContext} : normalizeStoryboardReview(raw); if (kind === 'script') remember(pipeline, data, 'script-review', review); else { data.storyboard.review = review; remember(pipeline, data, 'storyboard-review', review); } setPending(pipeline, pendingKey, ''); render(); toast(`${reviewer} 已返回评分与意见`); } catch (error) { setPending(pipeline, pendingKey, ''); render(); throw error; } }
  async function reviseScript(pipeline) { const data = updateInputData(pipeline); const review = data.history.find(item => item.type === 'script-review'); const text = data.scriptOutput || stageResultText(stageFor(pipeline, 'script')); if (!review || !text) throw new Error('没有可修改的剧本或审核意见'); const mode = scriptReviewMode(data, review); const generationContext = review.generationContext || scriptGenerationContext(data, mode); const promptItemId = mode.prompt; const pendingPipeline = pipeline; let started = false; setPending(pipeline, 'scriptReview', 'revision'); render(); try { pipeline = await startStageIfNeeded('script', pipeline, {onAccepted: () => { started = true; }}); setPending(pipeline, 'scriptReview', 'revision'); render(); const raw = await callTextModel(pipeline, 'script', promptItemId, scriptRevisionInstruction(mode, text, review, generationContext), generationContext, undefined, scriptRevisionSystemPrompt(pipeline, mode)); data.scriptMode = mode.key; data.scriptOutput = raw; remember(pipeline, data, 'script-revised', {text: raw.slice(0, 500), basedOn: review.at, scriptMode: mode.key, promptItemId}); archiveScriptAsset(pipeline, raw); await completeStage('script', pipeline.pipeline_id, {text: raw, message: '剧本已按审核意见修改并保存'}); setPending(pipeline, 'scriptReview', ''); render(); toast('已按审核意见重新生成剧本，旧版本仍保留'); } catch (error) { if (started) { try { await completeStage('script', pipeline.pipeline_id, {status: 'failed', message: '按审核意见修改失败，请重试'}); } catch (_) {} } setPending(pendingPipeline, 'scriptReview', ''); setPending(pipeline, 'scriptReview', ''); render(); throw error; } }
  async function generateAssets(pipeline) { const data = updateInputData(pipeline); const source = data.scriptOutput || stageResultText(stageFor(pipeline, 'script')) || data.scriptDraft; if (!source.trim()) throw new Error('请先完成剧本生成'); pipeline = await startStageIfNeeded('assets', pipeline); const run = ensureAssetRun(pipeline); const signal = run?.controller?.signal; try { const outputs = await Promise.all(['episode_asset_character', 'episode_asset_scene', 'episode_asset_prop'].map(itemId => callTextAgent(pipeline, 'assets', itemId, `从以下剧本中提取${itemId.endsWith('character') ? '角色' : itemId.endsWith('scene') ? '场景' : '关键道具'}，只返回 JSON，格式为 {"assets":[{"name":"","type":"character|scene|prop|other","prompt":""}]}。\n\n${source}`, 'text', signal))); const next = localData(pipeline); const extracted = outputs.flatMap(text => parseAssets(text)); const previous = new Map(next.assets.map(item => [`${item.type}:${item.name}`, item])); const used = new Set(); next.assets = extracted.filter((item, index, list) => list.findIndex(value => `${value.type}:${value.name}` === `${item.type}:${item.name}`) === index).map((item, index) => { const previousItem = previous.get(`${item.type}:${item.name}`); return {...item, ...(previousItem || {}), id: uniqueAssetId({...item, ...(previousItem || {})}, index, used)}; }); writeWorkspace(pipeline, next); await completeStage('assets', pipeline.pipeline_id, {text: JSON.stringify(next.assets), message: '资产提取已完成'}); clearAssetPoll(pipeline.pipeline_id); toast('角色、场景和道具提示词已生成'); } catch (error) { if (run?.stopRequested || error?.name === 'AbortError') { await loadPipelines().catch(() => {}); return; } if (run?.controller) run.controller.abort(); try { await completeStage('assets', pipeline.pipeline_id, {status: 'failed', message: error?.message || '资产提取失败'}); } catch (_) {} throw error; } }
  async function pollImageTask(pipeline, assetId, jobId) { try { const snapshot = await api(`/api/asset-registry/workspace-jobs/${encodeURIComponent(jobId)}`); const job = snapshot.job || snapshot; const status = String(job.status || '').toLowerCase(); if (status === 'succeeded') { const output = (job.links || []).find(link => link?.entity_type === 'asset' && link?.relation === 'output'); const data = localData(pipeline); const item = data.assets.find(value => value.id === assetId); if (!item) return; item.assetId = String(output?.entity_id || ''); item.preview = item.assetId ? `/api/asset-registry/assets/${encodeURIComponent(item.assetId)}/media` : ''; item.status = item.preview ? 'ready' : 'pending'; writeWorkspace(pipeline, data); render(); toast(item.preview ? `${item.name} 已生成` : '图片任务完成但未返回输出资产', !item.preview); return; } if (['failed', 'canceled', 'interrupted'].includes(status)) throw new Error(job.error?.message || job.error || '图片任务未成功完成'); window.setTimeout(() => pollImageTask(pipeline, assetId, jobId), 1800); } catch (error) { const data = localData(pipeline); const item = data.assets.find(value => value.id === assetId); if (item) { item.status = 'pending'; writeWorkspace(pipeline, data); render(); } toast(error.message || '图片生成失败', true); } }
  async function assetGenerate(pipeline, assetId) { const data = localData(pipeline); const item = data.assets.find(value => value.id === assetId); if (!item) throw new Error('未找到对应资产卡片'); const selection = selectedProviderModel(pipeline, 'asset_image', 'image'); if (!selection.provider_id || !selection.model) throw new Error('请先选择图片模型'); item.status = 'running'; writeWorkspace(pipeline, data); render(); try { const context = contextParams(pipeline, stageFor(pipeline, 'assets')); const result = await api('/api/online-image', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt: item.prompt, provider_id: selection.provider_id, model: selection.model, size: '1024x1024', n: 1, production_context: {project_id: context.project_id, entity_id: context.entity_id, canvas_id: context.canvas_id}})}); const preview = String(result.images?.[0] || result.urls?.[0] || (result.image_items || [])[0]?.url || ''); if (preview) { item.preview = preview; item.assetId = String(result.asset_ids?.[0] || result.image_items?.[0]?.asset_id || ''); item.status = 'ready'; writeWorkspace(pipeline, data); render(); toast(`${item.name} 已生成`); return; } const jobId = String(result.job_id || ''); if (jobId) { toast(`${item.name} 已提交后台生成`); pollImageTask(pipeline, assetId, jobId); return; } throw new Error('图片任务已完成但未返回图片地址'); } catch (error) { item.status = 'pending'; writeWorkspace(pipeline, data); render(); toast(error.message || '图片生成失败', true); } }
  async function generateOutline(pipeline) { const data = updateInputData(pipeline); const source = storyboardSource(pipeline, data); const targetDuration = String(q('[data-script-duration]')?.value || '').trim() || '按剧本实际时长判断'; if (!source.trim()) throw new Error('请先完成剧本生成'); if (!assetDocumentReady(pipeline, data)) throw new Error('请先生成资产设定文档'); const pendingImages = pendingAssetImages(pipeline, data); pipeline = await startStageIfNeeded('video', pipeline, {asset_document_ready: true}); if (pendingImages) toast(`提示：还有 ${pendingImages} 项资产图片未生成或未完成，不影响分镜大纲生成`); const raw = await callTextAgent(pipeline, 'video', 'episode_prompt_segment_planning', `请严格按照当前剧集提示词库中的 episode_prompt_segment_planning 文档，只返回一个合法 JSON 对象，不要 Markdown、不要解释。顶层字段必须严格是 coreConflict、protagonistMotivation、informationGain、fiveActs、sceneQuality、shots；每个 shot 必须严格是 index、title、scriptContent、shotType、keyBeats。shots 数量要匹配目标时长，避免切得过碎；资产连续性和武侠题材的身位、出招、兵器、空间方位必须写入可拍内容与关键节拍。\n\n目标时长：${targetDuration}\n\n剧本：\n${source}\n\n资产设定文档：\n${JSON.stringify(data.assets)}`); const outline = normalizeStoryboardOutline(raw); const next = localData(pipeline); next.storyboard.outline = JSON.stringify(outline, null, 2); writeWorkspace(pipeline, next); render(); toast('分镜规划导演 agent 已生成符合模板的大纲'); }
  async function splitShots(pipeline) { const data = updateInputData(pipeline); if (!outlineIsReady(data.storyboard.outline)) throw new Error('请先补齐并确认大纲字段'); const raw = await callTextAgent(pipeline, 'video', 'episode_prompt_segment_split', `请严格按照当前剧集提示词库中的 episode_prompt_segment_split 文档，只返回一个合法 JSON 对象，不要 Markdown、不要解释。请根据以下步骤1大纲拆分具体镜头，保持 shots 数量、顺序和主线因果，不要丢失 scriptContent 与 keyBeats。\n\n步骤1大纲：\n${data.storyboard.outline}`); data.storyboard.shots = parseShots(raw, data.storyboard.outline); if (!data.storyboard.shots.length) throw new Error('分镜拆分返回为空，无法进入下一步'); writeWorkspace(pipeline, data); render(); toast(`分镜师 agent 已拆分 ${data.storyboard.shots.length} 个镜头`); }
  async function reviseStoryboard(pipeline) { const data = updateInputData(pipeline); const review = data.storyboard.review; if (!review || !data.storyboard.shots.length) throw new Error('没有可修改的分镜或审核意见'); const feedback = storyboardReviewFeedback(review); if (feedback.issues.length < 2 || feedback.suggestions.length < 2 || feedback.priority.length < 2) throw new Error('审核意见不完整，无法提交重拆分'); const outline = parseJson(data.storyboard.outline) || data.storyboard.outline; const revisionInput = {outline, currentShots: data.storyboard.shots, reviewFeedback: feedback}; setPending(pipeline, 'storyboardReview', 'revision'); render(); try { const raw = await callTextAgent(pipeline, 'video', 'episode_prompt_segment_split', `你是负责执行审核修改的分镜师 agent。必须先读取 reviewFeedback，再严格按 priority 顺序执行，并逐条落实全部 issues 和 suggestions；每条意见至少要改动一个对应镜头字段，修改必须体现在 durationSec、cameraPosition、shotSize、movement、transitionIntent、keyBeats、scriptContent 之一。严禁原样复述 currentShots，严禁只解释不改稿。保持镜头数量、index 顺序、角色资产、场景资产和主线因果连续。输出完整步骤2 JSON，顶层字段和 shots 字段必须遵循当前 episode_prompt_segment_split 文档，且只返回裸 JSON。生成前自检：镜头总数不变、每条 priority 已执行、每条 issue/suggestion 都有对应字段变化。\n\n输入载荷：\n${JSON.stringify(revisionInput, null, 2)}`); const nextShots = parseShots(raw, data.storyboard.outline); if (!nextShots.length) throw new Error('按审核意见重拆返回为空，无法更新分镜'); if (nextShots.length !== data.storyboard.shots.length) throw new Error('分镜拆分 agent 未保持镜头数量不变，请重新执行'); if (!storyboardRevisionChanged(data.storyboard.shots, nextShots)) throw new Error('分镜拆分 agent 未执行审核意见：返回结果未改变任何镜头字段'); data.storyboard.shots = nextShots; remember(pipeline, data, 'storyboard-revised', {text: JSON.stringify(nextShots).slice(0, 500), basedOn: review.at, promptItemId: 'episode_prompt_segment_split', reviewFeedback: feedback}); setPending(pipeline, 'storyboardReview', ''); writeWorkspace(pipeline, data); render(); toast('已按审核意见逐条修改并重新拆分分镜'); } catch (error) { setPending(pipeline, 'storyboardReview', ''); render(); throw error; } }
  async function generateVideoScripts(pipeline) {
    if (pendingFor(pipeline, 'video_scripts')) return;
    setPending(pipeline, 'video_scripts', true);
    try {
      const data = updateInputData(pipeline); render(); if (!data.storyboard.shots.length) throw new Error('请先拆分具体分镜'); const review = data.storyboard.review; pipeline = await startStageIfNeeded('audio_compose', pipeline, {storyboard_ready: true}); if (!review || review.status !== 'passed') toast(review ? `提示：分镜审核 ${review.score} 分，仍可继续生成视频脚本` : '提示：尚未完成分镜审核，仍可继续生成视频脚本'); const raw = await callTextAgent(pipeline, 'audio_compose', 'episode_prompt_seedance_scene', `请为以下已拆分分镜生成逐镜头详细视频制作脚本，只返回 JSON，格式为 {"scripts":[{"shotId":"","title":"","prompt":"","negative":"","assets":""}]}。即使分镜审核未通过，也要按当前分镜生成脚本，不要自行阻断。\n\n${JSON.stringify(data.storyboard.shots)}`); const next = localData(pipeline); next.videoScripts = parseVideoScripts(raw, next.storyboard.shots); writeWorkspace(pipeline, next); render(); toast('高级单镜提示词工程师 agent 已生成视频脚本');
    } finally { setPending(pipeline, 'video_scripts', false); render(); }
  }
  async function completeStoryboard(pipeline) { const data = updateInputData(pipeline); if (!data.storyboard.shots.length) throw new Error('请先拆分分镜'); await completeStage('video', pipeline.pipeline_id, {text: JSON.stringify({outline: data.storyboard.outline, shots: data.storyboard.shots}), message: '分镜已确认'}); switchStep('audio_compose'); }
  async function completeVideoScript(pipeline) { const data = updateInputData(pipeline); if (!data.videoScripts.length) throw new Error('请先生成详细视频脚本'); await completeStage('audio_compose', pipeline.pipeline_id, {text: JSON.stringify(data.videoScripts), message: '视频脚本已确认'}); }
  function videoRequestKey(pipeline, videoId) { return `${pipeline?.pipeline_id || ''}\u0000${videoId || ''}`; }
  function videoContextProjectId(pipeline) { return String(pipeline?.project_id || state.projectId || ''); }
  function videoContextScopeKey(pipeline, actorId = state.videoActorId) {
    return JSON.stringify([String(actorId || ''), videoContextProjectId(pipeline), String(pipeline?.pipeline_id || '')]);
  }
  function videoContextPreferenceKey(actorId, projectId, pipelineId) {
    return `${VIDEO_CONTEXT_STORAGE_PREFIX}:${encodeURIComponent(actorId)}:${encodeURIComponent(projectId)}:${encodeURIComponent(pipelineId)}`;
  }
  function saveVideoContextPreference() {
    if (!videoContext.actorId || !videoContext.projectId || !videoContext.pipelineId) return;
    try {
      localStorage.setItem(videoContextPreferenceKey(videoContext.actorId, videoContext.projectId, videoContext.pipelineId),
        JSON.stringify({project_id: videoContext.projectId, pipeline_id: videoContext.pipelineId,
          canvas_id: videoContext.canvasId, entity_id: videoContext.entityId}));
    } catch (_) {}
  }
  function videoContextMatches(pipeline, generation, scopeKey = videoContextScopeKey(pipeline)) {
    return videoContext.generation === generation && videoContext.scopeKey === scopeKey &&
      videoContext.projectId === videoContextProjectId(pipeline) &&
      state.projectId === videoContext.projectId && currentPipeline()?.pipeline_id === pipeline?.pipeline_id;
  }
  function activateVideoContext(pipeline) {
    if (!pipeline?.pipeline_id) return;
    const projectId = videoContextProjectId(pipeline);
    const pipelineId = String(pipeline.pipeline_id);
    const actorId = String(state.videoActorId || '');
    const scopeKey = videoContextScopeKey(pipeline, actorId);
    if (videoContext.scopeKey === scopeKey) return;
    videoContext.controller?.abort();
    const generation = videoContext.generation + 1;
    const production = pipeline.production_context && typeof pipeline.production_context === 'object' ? pipeline.production_context : {};
    let canvasId = String(production.canvas_id || '');
    let entityId = String(production.entity_id || '');
    if (actorId) {
      try {
        const raw = localStorage.getItem(videoContextPreferenceKey(actorId, projectId, pipelineId));
        if (raw !== null) {
          const saved = JSON.parse(raw);
          canvasId = saved?.project_id === projectId && saved?.pipeline_id === pipelineId ? String(saved.canvas_id || '') : '';
          entityId = saved?.project_id === projectId && saved?.pipeline_id === pipelineId ? String(saved.entity_id || '') : '';
        }
      } catch (_) { canvasId = ''; entityId = ''; }
    }
    Object.assign(videoContext, {scopeKey, projectId, pipelineId, actorId, canvasId, entityId,
      canvases: [], nodes: [], loading: 'canvases', error: '', generation, controller: null});
    void loadVideoCanvases(pipeline, generation, scopeKey);
  }
  function videoContextReady(pipeline) {
    const scopeKey = videoContextScopeKey(pipeline);
    return videoContext.scopeKey === scopeKey && !videoContext.loading && !videoContext.error &&
      Boolean(videoContext.canvasId && videoContext.entityId &&
        videoContext.canvases.some(item => item.canvas_id === videoContext.canvasId && item.project_id === videoContext.projectId) &&
        videoContext.nodes.some(item => item.entity_id === videoContext.entityId));
  }
  function videoContextMessage(pipeline) {
    const sameScope = videoContext.scopeKey === videoContextScopeKey(pipeline);
    if (!sameScope || videoContext.loading === 'canvases') return '正在读取当前项目的真实画布与节点…';
    if (videoContext.loading === 'topology') return '正在读取所选画布拓扑…';
    if (videoContext.error) return videoContext.error;
    if (!videoContext.canvases.length) return '当前项目没有可选画布。请先前往项目中心创建画布与实体节点；本页不会自动修改画布。';
    if (!videoContext.canvasId) return '请选择目标画布和实体节点后，才能提交新的生成或导出请求。';
    if (!videoContext.canvases.some(item => item.canvas_id === videoContext.canvasId && item.project_id === videoContext.projectId)) return '已保存的画布当前不可见或已删除；请明确重新选择，不会自动切换。';
    if (!videoContext.nodes.length) return '所选画布没有可选实体节点；请先在项目画布中创建节点。';
    if (!videoContext.entityId) return '请选择该画布中的真实实体节点。';
    if (!videoContext.nodes.some(item => item.entity_id === videoContext.entityId)) return '已保存的实体节点已删除或不属于此画布；请明确重新选择。';
    return '上下文已从当前项目的画布拓扑读回；后端仍会在提交时重新校验权限与归属。';
  }
  function videoCanvasOptionLabel(item) { return String(item.title || item.name || item.canvas_id || '未命名画布'); }
  function videoNodeOptionLabel(item) { return String(item.label || item.title || item.name || item.entity_id || '未命名实体'); }
  async function loadVideoCanvases(pipeline, generation, scopeKey) {
    const controller = new AbortController();
    videoContext.controller = controller;
    try {
      const result = await api(`/api/canvases?project_id=${encodeURIComponent(videoContext.projectId)}`, {signal: controller.signal});
      if (!videoContextMatches(pipeline, generation, scopeKey)) return;
      videoContext.canvases = (Array.isArray(result.canvases) ? result.canvases : [])
        .filter(item => item && item.project_id === videoContext.projectId && typeof item.canvas_id === 'string');
      if (!videoContext.canvasId) {
        videoContext.loading = '';
        videoContext.nodes = [];
        render();
        return;
      }
      const selected = videoContext.canvases.find(item => item.canvas_id === videoContext.canvasId);
      if (!selected) {
        videoContext.loading = '';
        videoContext.nodes = [];
        render();
        return;
      }
      await loadVideoTopology(pipeline, generation, scopeKey, videoContext.canvasId);
    } catch (error) {
      if (error?.name === 'AbortError' || !videoContextMatches(pipeline, generation, scopeKey)) return;
      videoContext.loading = '';
      videoContext.error = `画布列表读取失败：${String(error?.message || '请求失败')}`;
      render();
    }
  }
  async function loadVideoTopology(pipeline, generation, scopeKey, canvasId) {
    videoContext.controller?.abort();
    const controller = new AbortController();
    videoContext.controller = controller;
    videoContext.loading = 'topology';
    videoContext.error = '';
    videoContext.nodes = [];
    render();
    try {
      const topology = await api(`/api/canvases/${encodeURIComponent(canvasId)}`, {signal: controller.signal});
      if (!videoContextMatches(pipeline, generation, scopeKey) || videoContext.canvasId !== canvasId) return;
      if (topology?.canvas_id !== canvasId || !Array.isArray(topology.nodes)) throw new Error('返回的画布拓扑与所选画布不匹配');
      videoContext.nodes = topology.nodes.filter(item => item && typeof item.entity_id === 'string' && item.entity_id);
      videoContext.loading = '';
      render();
    } catch (error) {
      if (error?.name === 'AbortError' || !videoContextMatches(pipeline, generation, scopeKey) || videoContext.canvasId !== canvasId) return;
      videoContext.loading = '';
      videoContext.error = `画布拓扑读取失败：${String(error?.message || '请求失败')}`;
      videoContext.nodes = [];
      render();
    }
  }
  async function verifiedVideoContext(pipeline) {
    activateVideoContext(pipeline);
    const generation = videoContext.generation;
    const scopeKey = videoContext.scopeKey;
    const projectId = videoContextProjectId(pipeline);
    const canvasId = videoContext.canvasId;
    const entityId = videoContext.entityId;
    if (!videoContextReady(pipeline)) throw new Error(videoContextMessage(pipeline));
    const controller = new AbortController();
    try {
      const list = await api(`/api/canvases?project_id=${encodeURIComponent(projectId)}`, {signal: controller.signal});
      if (!videoContextMatches(pipeline, generation, scopeKey) || videoContext.canvasId !== canvasId || videoContext.entityId !== entityId) throw new Error('上下文已切换，请确认后重新点击');
      const ownedCanvas = (list.canvases || []).find(item => item?.canvas_id === canvasId && item?.project_id === projectId);
      if (!ownedCanvas) throw new Error('目标画布已删除、不可见或不属于当前项目');
      const topology = await api(`/api/canvases/${encodeURIComponent(canvasId)}`, {signal: controller.signal});
      if (!videoContextMatches(pipeline, generation, scopeKey) || videoContext.canvasId !== canvasId || videoContext.entityId !== entityId) throw new Error('上下文已切换，请确认后重新点击');
      if (topology?.canvas_id !== canvasId || !Array.isArray(topology.nodes) || !topology.nodes.some(item => item?.entity_id === entityId)) {
        throw new Error('目标实体已删除或不属于所选画布；请重新选择后再提交');
      }
      return {project_id: projectId, canvas_id: canvasId, entity_id: entityId};
    } catch (error) {
      if (videoContextMatches(pipeline, generation, scopeKey) && videoContext.canvasId === canvasId && videoContext.entityId === entityId) {
        videoContext.error = String(error?.message || '画布或实体上下文验证失败');
        videoContext.loading = '';
        render();
      }
      throw error;
    }
  }
  function changeVideoContextCanvas(canvasId) {
    const pipeline = currentPipeline();
    if (!pipeline) return;
    activateVideoContext(pipeline);
    videoContext.controller?.abort();
    videoContext.generation += 1;
    videoContext.canvasId = String(canvasId || '');
    videoContext.entityId = '';
    videoContext.nodes = [];
    videoContext.loading = '';
    videoContext.error = '';
    saveVideoContextPreference();
    if (videoContext.canvasId && videoContext.canvases.some(item => item.canvas_id === videoContext.canvasId && item.project_id === videoContext.projectId)) {
      void loadVideoTopology(pipeline, videoContext.generation, videoContext.scopeKey, videoContext.canvasId);
    } else {
      render();
    }
  }
  function changeVideoContextEntity(entityId) {
    const pipeline = currentPipeline();
    if (!pipeline) return;
    activateVideoContext(pipeline);
    videoContext.entityId = String(entityId || '');
    videoContext.generation += 1;
    videoContext.error = '';
    saveVideoContextPreference();
    render();
  }
  async function refreshVideoIdentity() {
    const generation = ++state.videoIdentityReadGeneration;
    let actorId = '';
    try {
      const identity = await api('/api/asset-auth/identity-binding');
      actorId = identity?.bound === true && typeof identity.user_id === 'string' ? identity.user_id : '';
    } catch (_) {}
    if (generation !== state.videoIdentityReadGeneration) return null;
    const changed = actorId !== state.videoActorId;
    state.videoActorId = actorId;
    if (changed) {
      videoContext.controller?.abort();
      videoContext.scopeKey = '';
      activateVideoContext(currentPipeline());
      render();
    }
    return actorId ? {actorId, generation} : null;
  }
  function assertCurrentVideoIdentity(identity) {
    if (!identity?.actorId || identity.generation !== state.videoIdentityReadGeneration || identity.actorId !== state.videoActorId) {
      throw new Error('主体身份查询未完成、已过期或发生变化；本次未提交，请确认身份后重试');
    }
  }
  function videoContextMarkup(pipeline) {
    activateVideoContext(pipeline);
    const ready = videoContextReady(pipeline);
    const selectedCanvasExists = videoContext.canvases.some(item => item.canvas_id === videoContext.canvasId && item.project_id === videoContext.projectId);
    const selectedEntityExists = videoContext.nodes.some(item => item.entity_id === videoContext.entityId);
    const canvasOptions = ['<option value=""' + (!videoContext.canvasId ? ' selected' : '') + '>请选择目标画布</option>']
      .concat(videoContext.canvases.map(item => `<option value="${esc(item.canvas_id)}"${item.canvas_id === videoContext.canvasId ? ' selected' : ''}>${esc(videoCanvasOptionLabel(item))}</option>`));
    if (videoContext.canvasId && !selectedCanvasExists) canvasOptions.push(`<option value="${esc(videoContext.canvasId)}" selected>已失效画布 · ${esc(videoContext.canvasId)}</option>`);
    const entityOptions = ['<option value=""' + (!videoContext.entityId ? ' selected' : '') + '>请选择实体节点</option>']
      .concat(videoContext.nodes.map(item => `<option value="${esc(item.entity_id)}"${item.entity_id === videoContext.entityId ? ' selected' : ''}>${esc(videoNodeOptionLabel(item))} · ${esc(item.entity_id)}</option>`));
    if (videoContext.entityId && !selectedEntityExists) entityOptions.push(`<option value="${esc(videoContext.entityId)}" selected>已失效实体 · ${esc(videoContext.entityId)}</option>`);
    const message = videoContextMessage(pipeline);
    const nextStep = videoContext.canvases.length === 0 && !videoContext.loading && !videoContext.error
      ? `<a class="btn" href="/static/pages/projects.html?project_id=${encodeURIComponent(videoContextProjectId(pipeline))}">前往项目中心查看画布</a>` : '';
    return `<div class="vs-context-controls" data-video-context-scope="${esc(videoContextProjectId(pipeline))}:${esc(pipeline?.pipeline_id || '')}">
      <label>目标画布<select data-video-context-canvas aria-label="选择目标画布" ${!videoContext.canvases.length || videoContext.loading === 'canvases' ? 'disabled' : ''}>${canvasOptions.join('')}</select></label>
      <label>目标实体<select data-video-context-entity aria-label="选择目标实体" ${!selectedCanvasExists || videoContext.loading === 'topology' || !videoContext.nodes.length ? 'disabled' : ''}>${entityOptions.join('')}</select></label>
      <p class="vs-task-note ${ready ? '' : 'is-warning'}" role="status" aria-live="polite">${esc(message)}</p>
      ${!state.videoActorId ? '<p class="vs-task-note is-warning">当前身份没有稳定绑定；选择仅留在本页面，不读取或写入其他账号的本地偏好。</p>' : ''}
      ${nextStep}
    </div>`;
  }
  function videoStatusLabel(item) {
    if (item.videoStatus === 'unknown') return '结果未知 · 禁止自动重提';
    if (item.videoStatus === 'submitting') return '提交结果待确认';
    if (item.videoStatus === 'running') return item.videoJobId ? '后台任务运行中' : '正在提交';
    if (item.videoStatus === 'canceled') return '已取消';
    if (item.videoStatus === 'failed') return '生成失败 · 可重试';
    return item.videoUrl ? '已生成视频' : '待生成';
  }
  function stopVideoPolls() {
    for (const entry of videoPolls.values()) {
      if (entry.timer) window.clearTimeout(entry.timer);
      entry.controller?.abort();
    }
    videoPolls.clear();
  }
  function scheduleVideoPoll(pipeline, videoId, jobId, delay = 0) {
    if (!jobId || document.hidden || videoPolls.has(jobId)) return;
    const entry = {timer: 0, controller: null};
    entry.timer = window.setTimeout(() => {
      entry.timer = 0;
      void pollVideoTask(pipeline, videoId, jobId, entry);
    }, delay);
    videoPolls.set(jobId, entry);
  }
  function resumeVideoPolls(pipeline = currentPipeline()) {
    if (!pipeline || document.hidden) return;
    const data = localData(pipeline);
    for (const item of data.videoScripts) {
      if (item.videoStatus === 'running' && item.videoJobId) scheduleVideoPoll(pipeline, item.id, item.videoJobId, 250);
    }
    if (data.videoExportStatus === 'running' && data.videoExportJobId) scheduleVideoPoll(pipeline, '__export__', data.videoExportJobId, 250);
  }
  async function pollVideoTask(pipeline, videoId, jobId, entry = videoPolls.get(jobId)) {
    if (!entry || videoPolls.get(jobId) !== entry || document.hidden) return;
    const controller = new AbortController();
    entry.controller = controller;
    try {
      const result = await api(`/api/video-tasks/${encodeURIComponent(jobId)}`, {signal: controller.signal});
      if (videoPolls.get(jobId) !== entry) return;
      const status = String(result.status || '').toLowerCase();
      if (['succeeded', 'failed', 'canceled', 'interrupted'].includes(status)) {
        videoPolls.delete(jobId);
        const data = localData(pipeline);
        if (videoId === '__export__') {
          data.videoExportJobStatus = status;
          data.videoExportError = String(result.error?.message || result.error?.code || '');
          data.videoExportRemoteMayContinue = Boolean(result.remote_may_continue_or_bill);
          if (status === 'succeeded') {
            data.videoExportUrl = String(result.content_url || '');
            data.videoExportAssetId = String(result.asset_id || '');
            data.videoExportStatus = data.videoExportUrl ? 'ready' : 'failed';
            data.videoExportError = data.videoExportUrl ? '' : '导出完成，但未返回鉴权内容地址';
          } else {
            data.videoExportStatus = status === 'canceled' ? 'canceled' : 'failed';
          }
          data.videoExportSubmission = null;
          writeWorkspace(pipeline, data);
          render();
          toast(data.videoExportStatus === 'ready' ? '合并视频已导出' : data.videoExportError || '本地导出任务已结束', data.videoExportStatus !== 'ready');
          return;
        }
        const item = data.videoScripts.find(value => value.id === videoId);
        if (!item) return;
        item.videoJobStatus = status;
        item.videoRemoteMayContinue = Boolean(result.remote_may_continue_or_bill);
        item.videoError = String(result.error?.message || result.error?.code || '');
        if (status === 'succeeded') {
          item.videoUrl = String(result.content_url || '');
          item.assetId = String(result.asset_id || '');
          item.videoStatus = item.videoUrl ? 'ready' : 'failed';
          item.videoError = item.videoUrl ? '' : '任务已完成，但未返回鉴权内容地址';
        } else if (status === 'interrupted' && result.result_unknown) {
          item.videoStatus = 'unknown';
          item.videoError = '创建结果未知；为避免重复计费，未自动重新提交。';
        } else {
          item.videoStatus = status === 'canceled' ? 'canceled' : 'failed';
        }
        item.videoSubmission = null;
        writeWorkspace(pipeline, data);
        render();
        toast(status === 'succeeded' && item.videoUrl ? `${item.title} 视频已生成` : item.videoError || '视频任务已结束', status !== 'succeeded');
        return;
      }
      entry.controller = null;
      entry.timer = window.setTimeout(() => {
        entry.timer = 0;
        void pollVideoTask(pipeline, videoId, jobId, entry);
      }, 1800);
    } catch (error) {
      if (videoPolls.get(jobId) !== entry || error?.name === 'AbortError' || document.hidden) return;
      videoPolls.delete(jobId);
      const data = localData(pipeline);
      if (videoId === '__export__') {
        data.videoExportError = '状态查询暂时失败，可点击“继续查询导出”恢复。';
        writeWorkspace(pipeline, data);
        render();
      } else {
        const item = data.videoScripts.find(value => value.id === videoId);
        if (item) {
          // 保留 job_id 与运行状态；失败只停止本页轮询，不伪报任务终态。
          item.videoError = '状态查询暂时失败，可点击“继续查询”恢复。';
          writeWorkspace(pipeline, data);
          render();
        }
      }
      toast(error.message || '视频任务查询暂时失败；可恢复查询', true);
    }
  }
  function makeVideoIdempotencyKey() {
    if (window.crypto?.randomUUID) return `gwb-video-${window.crypto.randomUUID()}`;
    return `gwb-video-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  }
  async function generateVideo(pipeline, videoId) {
    const identity = await refreshVideoIdentity();
    assertCurrentVideoIdentity(identity);
    const data = updateInputData(pipeline);
    const item = data.videoScripts.find(value => value.id === videoId);
    if (!item || item.videoStatus === 'unknown') return;
    if (item.videoJobId && item.videoStatus === 'running') {
      scheduleVideoPoll(pipeline, videoId, item.videoJobId);
      return;
    }
    const requestKey = videoRequestKey(pipeline, videoId);
    if (videoRequests.has(requestKey)) return;
    let submission = item.videoSubmission;
    const recoveringSubmission = Boolean(submission);
    if (submission && (!submission.actor_id || submission.actor_id !== state.videoActorId)) {
      throw new Error('旧提交结果待确认且当前主体身份不匹配；禁止以另一账号重新提交，请回到原账号核查');
    }
    if (!submission) {
      if (!String(item.prompt || '').trim()) throw new Error('请先填写主提示词');
      const selection = selectedVideoModel(pipeline);
      if (!selection.provider_id || !selection.model) throw new Error('请先选择视频模型');
      videoRequests.add(requestKey);
      let context;
      try { context = await verifiedVideoContext(pipeline); assertCurrentVideoIdentity(identity); }
      catch (error) { videoRequests.delete(requestKey); throw error; }
      const prompt = [item.prompt, item.negative ? `避免出现：${item.negative}` : '', item.assets ? `资产文本引用：${item.assets}` : ''].filter(Boolean).join('\n\n');
      submission = {
        actor_id: String(state.videoActorId || ''),
        idempotency_key: makeVideoIdempotencyKey(),
        payload: {prompt, provider_id: selection.provider_id, model: selection.model, duration: 5, aspect_ratio: '16:9',
          production_context: {project_id: context.project_id, entity_id: context.entity_id, canvas_id: context.canvas_id}}
      };
      item.videoSubmission = submission;
    }
    item.videoStatus = 'submitting';
    item.videoJobId = '';
    item.videoUrl = '';
    item.assetId = '';
    item.videoError = '';
    item.videoRemoteMayContinue = false;
    writeWorkspace(pipeline, data);
    videoRequests.add(requestKey);
    render();
    try {
      assertCurrentVideoIdentity(identity);
      const result = await api('/api/video-tasks', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': submission.idempotency_key}, body: JSON.stringify(submission.payload)});
      const jobId = String(result.job_id || '');
      if (!jobId) throw new Error('视频任务未返回稳定 job_id；恢复时将沿用原幂等键');
      const currentData = localData(pipeline);
      const current = currentData.videoScripts.find(value => value.id === videoId);
      if (!current) return;
      current.videoJobId = jobId;
      current.videoStatus = ['queued', 'running'].includes(String(result.status || '').toLowerCase()) ? 'running' : String(result.status || 'running');
      current.videoJobStatus = String(result.status || 'queued');
      current.videoSubmission = null;
      current.videoError = '';
      writeWorkspace(pipeline, currentData);
      render();
      scheduleVideoPoll(pipeline, videoId, jobId, 250);
      toast(`${current.title} 已提交后台生成`);
    } catch (error) {
      const currentData = localData(pipeline);
      const current = currentData.videoScripts.find(value => value.id === videoId);
      if (current) {
        // 本次拒绝不能证明先前未知提交未被受理，恢复请求始终保留原键与载荷。
        const definitelyRejected = !recoveringSubmission && [400, 401, 403, 404, 422].includes(Number(error?.status));
        current.videoStatus = definitelyRejected ? 'failed' : 'submitting';
        current.videoSubmission = definitelyRejected ? null : submission;
        current.videoError = String(error?.message || '视频任务提交结果待确认');
        writeWorkspace(pipeline, currentData);
        render();
      }
      toast(error.message || '视频任务提交失败；可使用原幂等键恢复', true);
    } finally {
      videoRequests.delete(requestKey);
    }
  }
  async function generateVideoExport(pipeline) {
    const identity = await refreshVideoIdentity();
    assertCurrentVideoIdentity(identity);
    const data = updateInputData(pipeline);
    const requestKey = videoRequestKey(pipeline, '__export__');
    if (videoRequests.has(requestKey)) return;
    if (data.videoExportJobId && data.videoExportStatus === 'running') {
      scheduleVideoPoll(pipeline, '__export__', data.videoExportJobId);
      return;
    }
    const selected = data.videoScripts.filter(item => item.exportSelected && item.videoStatus === 'ready' && item.assetId);
    if (!selected.length && !data.videoExportSubmission) throw new Error('请至少选择一个已生成的视频镜头');
    let submission = data.videoExportSubmission;
    const recoveringSubmission = Boolean(submission);
    if (submission && (!submission.actor_id || submission.actor_id !== state.videoActorId)) {
      throw new Error('旧导出提交结果待确认且当前主体身份不匹配；请回到原账号核查');
    }
    if (!submission) {
      videoRequests.add(requestKey);
      let context;
      try { context = await verifiedVideoContext(pipeline); assertCurrentVideoIdentity(identity); }
      catch (error) { videoRequests.delete(requestKey); throw error; }
      submission = {
        actor_id: String(state.videoActorId || ''),
        idempotency_key: makeVideoIdempotencyKey(),
        payload: {asset_ids: selected.map(item => item.assetId), project_id: context.project_id,
          canvas_id: context.canvas_id, entity_id: context.entity_id, preset: 'h264_720p_30fps'}
      };
      data.videoExportSubmission = submission;
    }
    data.videoExportStatus = 'submitting';
    data.videoExportJobId = '';
    data.videoExportUrl = '';
    data.videoExportAssetId = '';
    data.videoExportError = '';
    data.videoExportRemoteMayContinue = false;
    writeWorkspace(pipeline, data);
    videoRequests.add(requestKey);
    render();
    try {
      assertCurrentVideoIdentity(identity);
      const result = await api('/api/video-exports', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': submission.idempotency_key}, body: JSON.stringify(submission.payload)});
      const jobId = String(result.job_id || '');
      if (!jobId) throw new Error('导出任务未返回稳定 job_id；恢复时将沿用原幂等键');
      const currentData = localData(pipeline);
      currentData.videoExportJobId = jobId;
      currentData.videoExportStatus = ['queued', 'running'].includes(String(result.status || '').toLowerCase()) ? 'running' : String(result.status || 'running');
      currentData.videoExportJobStatus = String(result.status || 'queued');
      currentData.videoExportSubmission = null;
      currentData.videoExportError = '';
      writeWorkspace(pipeline, currentData);
      render();
      scheduleVideoPoll(pipeline, '__export__', jobId, 250);
      toast('本地合并导出已提交');
    } catch (error) {
      const currentData = localData(pipeline);
      // 本次拒绝不能证明先前未知提交未被受理，恢复请求始终保留原键与载荷。
      const definitelyRejected = !recoveringSubmission && [400, 401, 403, 404, 422].includes(Number(error?.status));
      currentData.videoExportStatus = definitelyRejected ? 'failed' : 'submitting';
      currentData.videoExportSubmission = definitelyRejected ? null : submission;
      currentData.videoExportError = String(error?.message || '导出任务提交结果待确认');
      writeWorkspace(pipeline, currentData);
      render();
      toast(error.message || '导出提交失败；可使用原幂等键恢复', true);
    } finally {
      videoRequests.delete(requestKey);
    }
  }
  async function cancelVideoExport(pipeline) {
    const data = localData(pipeline);
    if (!data.videoExportJobId) return;
    const jobId = data.videoExportJobId;
    const active = videoPolls.get(jobId);
    if (active?.timer) window.clearTimeout(active.timer);
    active?.controller?.abort();
    videoPolls.delete(jobId);
    try {
      const result = await api(`/api/video-tasks/${encodeURIComponent(jobId)}/cancel`, {method: 'POST'});
      const currentData = localData(pipeline);
      currentData.videoExportStatus = String(result.status || '') === 'canceled' ? 'canceled' : 'running';
      currentData.videoExportJobStatus = String(result.status || '');
      currentData.videoExportRemoteMayContinue = Boolean(result.remote_may_continue_or_bill);
      currentData.videoExportError = '';
      writeWorkspace(pipeline, currentData);
      render();
      toast('本地视频导出已取消');
    } catch (error) {
      const currentData = localData(pipeline);
      currentData.videoExportStatus = 'running';
      currentData.videoExportError = '取消请求未确认，可恢复查询导出状态。';
      writeWorkspace(pipeline, currentData);
      render();
      resumeVideoPolls(pipeline);
      toast(error.message || '本地导出取消失败', true);
    }
  }
  async function cancelVideoTask(pipeline, videoId) {
    const data = localData(pipeline);
    const item = data.videoScripts.find(value => value.id === videoId);
    if (!item?.videoJobId) return;
    const jobId = item.videoJobId;
    const active = videoPolls.get(jobId);
    if (active?.timer) window.clearTimeout(active.timer);
    active?.controller?.abort();
    videoPolls.delete(jobId);
    try {
      const result = await api(`/api/video-tasks/${encodeURIComponent(jobId)}/cancel`, {method: 'POST'});
      const currentData = localData(pipeline);
      const current = currentData.videoScripts.find(value => value.id === videoId);
      if (!current) return;
      current.videoStatus = String(result.status || '') === 'canceled' ? 'canceled' : 'running';
      current.videoJobStatus = String(result.status || '');
      current.videoRemoteMayContinue = Boolean(result.remote_may_continue_or_bill);
      current.videoError = '';
      writeWorkspace(pipeline, currentData);
      render();
      toast(current.videoRemoteMayContinue ? '本地任务已取消；上游可能继续运行或计费' : '视频任务已取消');
    } catch (error) {
      const currentData = localData(pipeline);
      const current = currentData.videoScripts.find(value => value.id === videoId);
      if (current) {
        current.videoStatus = 'running';
        current.videoError = '取消请求未确认，可恢复查询任务状态。';
        writeWorkspace(pipeline, currentData);
        render();
        resumeVideoPolls(pipeline);
      }
      toast(error.message || '视频任务取消失败', true);
    }
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopVideoPolls(); else resumeVideoPolls(); });
  window.addEventListener('pagehide', stopVideoPolls);
  window.addEventListener('pageshow', () => { resumeVideoPolls(); void refreshVideoIdentity(); });
  window.addEventListener('focus', () => { void refreshVideoIdentity(); });
  window.addEventListener('storage', () => { void refreshVideoIdentity(); });
  function assetRecord(pipelineId, assetId) { const pipeline = pipelineFor(pipelineId); if (!pipeline) return null; const data = localData(pipeline); const item = data.assets.find(value => value.id === assetId); return item ? {pipeline, data, item} : null; }
  function openAssetDetail(pipelineId, assetId, returnFocus = null) { const record = assetRecord(pipelineId, assetId); const dialog = q('#assetDetailDialog'); if (!record || !dialog) return; state.detailReturnFocus = returnFocus || document.activeElement; dialog.dataset.pipelineId = record.pipeline.pipeline_id; dialog.dataset.assetId = record.item.id; dialog.innerHTML = `<form method="dialog" class="asset-detail-shell" data-floating-content><div class="asset-detail-head"><div><span class="asset-detail-kicker">资产设定</span><h2>${esc(record.item.name)}</h2><p>${esc(assetTypeLabel(record.item.type))} · ${esc(record.item.status === 'ready' ? '图片已生成' : '图片待生成')}</p></div><button class="icon-button" type="button" data-asset-detail-close aria-label="关闭资产详情" title="关闭">×</button></div><div class="asset-detail-body"><div class="asset-detail-media ${record.item.preview ? 'has-image' : ''}">${record.item.preview ? `<img data-detail-image src="${esc(record.item.preview)}" alt="${esc(record.item.name)}" title="双击查看大图">` : '<div class="asset-placeholder"><span>待生成图片</span><small>保存设定后可生成资产</small></div>'}</div><div class="asset-detail-fields"><label>资产名称<input data-asset-detail-field="name" value="${esc(record.item.name)}"></label><label>资产类型<select data-asset-detail-field="type"><option value="character"${record.item.type === 'character' ? ' selected' : ''}>角色</option><option value="scene"${record.item.type === 'scene' ? ' selected' : ''}>场景</option><option value="prop"${record.item.type === 'prop' ? ' selected' : ''}>道具</option><option value="other"${record.item.type === 'other' ? ' selected' : ''}>其他</option></select></label><label>资产设定提示词<textarea data-asset-detail-field="prompt" rows="9">${esc(record.item.prompt)}</textarea></label></div></div><div class="asset-detail-actions"><button class="btn" type="button" data-asset-detail-regenerate data-pipeline-id="${esc(record.pipeline.pipeline_id)}" data-asset-id="${esc(record.item.id)}">重新生成</button><span></span><button class="btn" type="button" data-asset-detail-close>取消</button><button class="btn primary" type="button" data-asset-detail-save>保存修改</button></div></form>`; if (!dialog.open) dialog.showModal(); dialog.querySelector('[data-asset-detail-field="name"]')?.focus(); }
  function openAssetImage(pipelineId, assetId) { const record = assetRecord(pipelineId, assetId); const dialog = q('#assetImageDialog'); if (!record || !record.item.preview || !dialog) return; dialog.innerHTML = `<div class="asset-image-shell" data-floating-content><div class="asset-image-head"><strong>${esc(record.item.name)}</strong><button class="icon-button" type="button" data-asset-image-close aria-label="关闭图片预览" title="关闭">×</button></div><img src="${esc(record.item.preview)}" alt="${esc(record.item.name)}"><p>双击卡片可编辑设定，点击空白关闭预览</p></div>`; if (!dialog.open) dialog.showModal(); }
  function saveAssetDetail(dialog) { const record = assetRecord(dialog.dataset.pipelineId, dialog.dataset.assetId); if (!record) throw new Error('资产卡片已不存在'); const name = String(dialog.querySelector('[data-asset-detail-field="name"]')?.value || '').trim(); const prompt = String(dialog.querySelector('[data-asset-detail-field="prompt"]')?.value || '').trim(); const type = String(dialog.querySelector('[data-asset-detail-field="type"]')?.value || 'other'); if (!name) throw new Error('资产名称不能为空'); if (!prompt) throw new Error('资产设定提示词不能为空'); record.item.name = name; record.item.prompt = prompt; record.item.type = ['character', 'scene', 'prop', 'other'].includes(type) ? type : 'other'; writeWorkspace(record.pipeline, record.data); dialog.close(); render(); toast('资产设定已保存'); }
  function closeAssetDialog(dialog) { if (dialog?.open) dialog.close(); }
  function syncStepParam(key) { const url = new URL(window.location.href); url.searchParams.set('step', key); history.replaceState(history.state, '', url); }
  function switchStep(key) { const step = STAGES.some(item => item.key === key) ? key : 'script'; state.activeStep = step; syncStepParam(step); render(); q(`#episodeStepNav [data-step="${CSS.escape(state.activeStep)}"]`)?.focus(); try { if (window.parent && window.parent !== window) { window.parent.postMessage({ type: 'episode-step-changed', step }, '*'); } } catch (_) {} }
  window.switchStep = switchStep;
  window.addEventListener('message', event => {
    if (event.data && event.data.type === 'navigate-step' && event.data.step) {
      switchStep(event.data.step);
    }
  });
  window.addEventListener('popstate', () => {
    const params = new URLSearchParams(window.location.search);
    const step = params.get('step');
    if (step && step !== state.activeStep) {
      state.activeStep = STAGES.some(item => item.key === step) ? step : 'script';
      render();
    }
  });
  document.addEventListener('click', event => {
    const dialog = event.target.closest('dialog');
    if (dialog && event.target === dialog) return closeAssetDialog(dialog);
    const knob = event.target.closest('[data-knob]');
    if (knob) {
      const type = knob.dataset.knob;
      const pipeline = currentPipeline();
      if (!pipeline) return;
      const data = localData(pipeline);
      if (type === 'genre') {
        const idx = (SCRIPT_GENRES.indexOf(data.scriptGenre) + 1) % SCRIPT_GENRES.length;
        data.scriptGenre = SCRIPT_GENRES[idx];
        toast(`题材预设已调整为：${data.scriptGenre}`);
      } else if (type === 'style') {
        const idx = (SCRIPT_STYLES.indexOf(data.scriptStyle) + 1) % SCRIPT_STYLES.length;
        data.scriptStyle = SCRIPT_STYLES[idx];
        toast(`视听风格已调整为：${data.scriptStyle}`);
      } else if (type === 'duration') {
        const idx = (SCRIPT_DURATIONS.indexOf(data.scriptDuration) + 1) % SCRIPT_DURATIONS.length;
        data.scriptDuration = SCRIPT_DURATIONS[idx];
        toast(`单集时长已调整为：${data.scriptDuration}`);
      }
      writeWorkspace(pipeline, data);
      render();
      return;
    }
    const target = event.target.closest('button');
    if (!target) return;
    const gotoBtn = target.closest('[data-goto-step]');
    if (gotoBtn) return switchStep(gotoBtn.dataset.gotoStep);
    const pipeline = target.dataset.pipelineId ? pipelineFor(target.dataset.pipelineId) : currentPipeline();
    const action = fn => { const message = actionMessage(target); if (message) toast(message); return Promise.resolve(fn()).catch(error => toast(error.message || '操作失败', true)); };
    if (target.matches('[data-history-item]')) {
      const at = Number(target.dataset.historyItem);
      const record = pipeline && localData(pipeline).history.find(item => item.at === at);
      if (record?.text) {
        const data = localData(pipeline);
        data.scriptOutput = record.text;
        writeWorkspace(pipeline, data);
        render();
        toast(`已加载历史版本 (${dateTime(at)})`);
      }
      return;
    }
    if (target.matches('#newEpisode,[data-open-create]')) return openCreate();
    if (target.matches('#closeCreate,#cancelCreate')) return closeCreate();
    if (target.matches('[data-retry]')) { toast('正在执行：重新读取剧集流水线'); return load(); }
    if (target.matches('[data-step]')) return switchStep(target.dataset.step);
    if (target.matches('[data-toggle-script-editor]')) { state.scriptEditorMode = state.scriptEditorMode === 'edit' ? 'preview' : 'edit'; render(); return; }
    if (target.matches('[data-confirm-next]')) return switchStep('assets');
    if (target.matches('[data-asset-group]')) { if (pipeline) { state.assetGroups[pipeline.pipeline_id] = target.dataset.assetGroup || 'all'; render(); } return; }
    if (target.matches('[data-asset-detail-close],[data-asset-image-close]')) return closeAssetDialog(target.closest('dialog'));
    if (target.matches('[data-asset-detail-save]')) return action(() => saveAssetDetail(q('#assetDetailDialog')));
    if (target.matches('[data-asset-detail-regenerate]')) { const assetId = target.dataset.assetId; closeAssetDialog(q('#assetDetailDialog')); return action(() => assetGenerate(pipeline, assetId)); }
    if (target.matches('[data-revise-script]')) return action(() => reviseScript(pipeline));
    if (target.matches('[data-revise-storyboard]')) return action(() => reviseStoryboard(pipeline));
    if (target.matches('[data-script-review]')) return action(() => sendReview(pipeline, 'script'));
    if (target.matches('[data-stage-stop]')) return action(() => stopStage(pipelineFor(target.dataset.pipelineId), target.dataset.stageStop || target.dataset.stage, false));
    if (target.matches('[data-stage-start],[data-stage-retry]')) { const key = target.dataset.stageStart || target.dataset.stageRetry; const task = key === 'script' ? generateScript : key === 'assets' ? generateAssets : key === 'video' ? generateOutline : generateVideoScripts; return action(() => task(pipelineFor(target.dataset.pipelineId))); }
    if (target.matches('[data-save-script]')) return action(() => saveScriptVersion(pipeline));
    if (target.matches('[data-assets-refresh]')) return action(() => generateAssets(pipeline));
    if (target.matches('[data-batch-generate-assets]')) return action(() => batchGenerateAssets(pipeline));
    if (target.matches('[data-asset-generate]')) return action(() => assetGenerate(pipeline, target.dataset.assetGenerate));
    if (target.matches('[data-asset-upload]')) { state.uploadAssetId = target.dataset.assetUpload; state.uploadPipelineId = target.dataset.pipelineId || pipeline?.pipeline_id || ''; return q('#asset-upload-input')?.click(); }
    if (target.matches('[data-outline-expand]')) { const field = target.closest('[data-outline-field-wrap]'); if (!field) return; const expanded = field.classList.toggle('is-expanded'); target.textContent = expanded ? '收起' : '展开'; target.setAttribute('aria-expanded', String(expanded)); return; }
    if (target.matches('[data-outline-generate]')) return action(() => generateOutline(pipeline));
    if (target.matches('[data-shots-generate]')) return action(() => splitShots(pipeline));
    if (target.matches('[data-storyboard-review]')) return action(() => sendReview(pipeline, 'storyboard'));
    if (target.matches('[data-video-scripts-generate]')) return action(() => generateVideoScripts(pipeline));
    if (target.matches('[data-video-generate]')) return action(() => generateVideo(pipeline, target.dataset.videoGenerate));
    if (target.matches('[data-video-cancel]')) return action(() => cancelVideoTask(pipeline, target.dataset.videoCancel));
    if (target.matches('[data-video-export]')) return action(() => generateVideoExport(pipeline));
    if (target.matches('[data-video-export-cancel]')) return action(() => cancelVideoExport(pipeline));
    if (target.matches('[data-complete-storyboard]')) return action(() => completeStoryboard(pipeline));
    if (target.matches('[data-save-video-script]')) return action(() => { updateInputData(pipeline); toast('视频脚本已保存到当前工作区'); });
    if (target.matches('[data-complete-video-script]')) return action(() => completeVideoScript(pipeline));
  });
  document.addEventListener('dblclick', event => {
    const image = event.target.closest('[data-asset-image],[data-detail-image]');
    if (image) { const pipelineId = image.dataset.pipelineId || q('#assetDetailDialog')?.dataset.pipelineId; const assetId = image.dataset.assetId || q('#assetDetailDialog')?.dataset.assetId; if (pipelineId && assetId) openAssetImage(pipelineId, assetId); return; }
    const card = event.target.closest('[data-asset-card]');
    if (card && !event.target.closest('button,input,textarea,select,a')) openAssetDetail(card.dataset.pipelineId, card.dataset.assetId, card);
  });
  document.addEventListener('focusin', event => { const card = event.target.closest('[data-asset-card]'); if (card) state.focusedAsset = {pipelineId: card.dataset.pipelineId, assetId: card.dataset.assetId}; });
  document.addEventListener('pointerover', event => { const card = event.target.closest('[data-asset-card]'); if (card) state.focusedAsset = {pipelineId: card.dataset.pipelineId, assetId: card.dataset.assetId}; });
  document.addEventListener('keydown', event => { if (!(['~', '`', 'Dead'].includes(event.key) || event.code === 'Backquote') || event.target.matches('input,textarea,select,[contenteditable="true"]')) return; const card = event.target.closest('[data-asset-card]'); const target = card || (state.focusedAsset && document.querySelector(`[data-asset-card][data-pipeline-id="${CSS.escape(state.focusedAsset.pipelineId)}"][data-asset-id="${CSS.escape(state.focusedAsset.assetId)}"]`)); if (!target) return; event.preventDefault(); openAssetDetail(target.dataset.pipelineId, target.dataset.assetId, target); });
  /* GW (v3.4): 「·」/Backquote（无 shift，即非 ~）返回项目制片页。与上一条资产卡
     快捷键共存：焦点或最近悬停在资产卡上时让位给资产详情。编辑类目标与弹窗打开时忽略。 */
  function gotoProjectBoard() {
    if (window.parent && window.parent !== window) {
      try { window.parent.postMessage({ type: 'episode-open-project-board', projectId: state.projectId || '' }, location.origin); return; } catch (error) {}
    }
    const url = new URL('/static/pages/projects.html', window.location.origin);
    if (state.projectId) url.searchParams.set('project_id', state.projectId);
    window.location.href = url.href;
  }
  document.addEventListener('keydown', event => {
    const isDotKey = event.key === '·' || event.key === '`' || event.code === 'Backquote';
    if (!isDotKey || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.defaultPrevented) return;
    if (event.target.matches('input,textarea,select,[contenteditable="true"]')) return;
    if (document.querySelector('dialog[open]')) return;
    // 资产卡快捷键优先（上一条监听器）：卡片聚焦或存在最近资产焦点时让位。
    if (event.target.closest('[data-asset-card]')) return;
    if (state.focusedAsset && document.querySelector(`[data-asset-card][data-pipeline-id="${CSS.escape(state.focusedAsset.pipelineId)}"][data-asset-id="${CSS.escape(state.focusedAsset.assetId)}"]`)) return;
    event.preventDefault();
    gotoProjectBoard();
  });
  document.addEventListener('change', event => { if (event.target.matches('[data-video-export-select]')) { const pipeline = currentPipeline(); if (!pipeline) return; const data = localData(pipeline); const item = data.videoScripts.find(value => value.id === event.target.dataset.videoExportSelect); if (item) { item.exportSelected = Boolean(event.target.checked); writeWorkspace(pipeline, data); render(); } return; } if (event.target.matches('#asset-upload-input')) { const file = event.target.files?.[0]; const pipeline = pipelineFor(state.uploadPipelineId); const data = localData(pipeline); const item = data.assets.find(value => value.id === state.uploadAssetId); if (item && file) { const reader = new FileReader(); reader.onload = () => { item.preview = String(reader.result || ''); item.status = 'ready'; writeWorkspace(pipeline, data); render(); toast(`${item.name} 已替换为本地图片`); }; reader.readAsDataURL(file); } event.target.value = ''; state.uploadAssetId = ''; state.uploadPipelineId = ''; } if (event.target.matches('[data-stage-model]')) { const pipeline = currentPipeline(); if (!pipeline) return; const data = localData(pipeline); const [provider_id, model] = String(event.target.value || '').split(':::'); data.models[event.target.dataset.stageModel] = {provider_id, model}; writeWorkspace(pipeline, data); render(); toast('当前阶段模型已保存'); } if (event.target.matches('[data-script-mode]')) { const pipeline = currentPipeline(); if (!pipeline) return; const data = updateInputData(pipeline); render(); toast(`剧本输入模式已切换为：${SCRIPT_MODES.find(item => item.key === data.scriptMode)?.label || SCRIPT_MODES[0].label}`); } if (event.target.matches('[data-video-context-canvas]')) { changeVideoContextCanvas(event.target.value); return; } if (event.target.matches('[data-video-context-entity]')) { changeVideoContextEntity(event.target.value); return; } if (event.target.matches('[data-pipeline-select]')) { state.selected = pipelineFor(event.target.value); state.selectedPipelineId = String(event.target.value || ''); syncAuraScope(state.selected); activateVideoContext(state.selected); render(); } });
  document.addEventListener('input', event => { if (event.target.matches('[data-script-draft]')) { const counter = q('#scriptDraftCharCount'); if (counter) counter.textContent = `CHARS: ${String(event.target.value.length).padStart(2, '0')}/120`; } if (event.target.matches('[data-script-pacing]')) { const label = q('[data-script-pacing-label]'); const value = SCRIPT_PACINGS[Number(event.target.value)] || '1.25x'; if (label) label.textContent = `PACING: ${value} SPEED`; } if (!event.target.matches('[data-script-draft],[data-script-output],[data-script-pacing],[data-outline],[data-outline-field],[data-outline-act],[data-shot-field],[data-video-field]')) return; const pipeline = currentPipeline(); if (pipeline) { const data = updateInputData(pipeline); if (event.target.matches('[data-outline-field],[data-outline-act]')) syncOutlineActions(data); } });
  q('#episodeStepNav')?.addEventListener('keydown', event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !currentPipeline()) return; event.preventDefault(); const index = Math.max(0, STAGES.findIndex(item => item.key === state.activeStep)); const next = event.key === 'ArrowRight' ? (index + 1) % STAGES.length : event.key === 'ArrowLeft' ? (index - 1 + STAGES.length) % STAGES.length : event.key === 'Home' ? 0 : STAGES.length - 1; switchStep(STAGES[next].key); });
  document.addEventListener('submit', event => {
    if (event.target.matches('#createForm,[data-create-form]')) {
      create(event);
    }
  });
  window.addEventListener('studio-lang-change', render);
  window.addEventListener('episode-asset-upload', event => { const pipeline = pipelineFor(state.uploadPipelineId); const file = event.detail?.file; const data = localData(pipeline); const item = data.assets.find(value => value.id === state.uploadAssetId); if (item && file) { const reader = new FileReader(); reader.onload = () => { item.preview = String(reader.result || ''); item.status = 'ready'; writeWorkspace(pipeline, data); render(); }; reader.readAsDataURL(file); } });
  q('#assetDetailDialog')?.addEventListener('close', () => { const target = state.detailReturnFocus; state.detailReturnFocus = null; if (target?.isConnected) target.focus(); });
  q('#assetImageDialog')?.addEventListener('close', () => { state.detailReturnFocus?.focus?.(); });
  load();
})();
