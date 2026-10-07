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

(function(){
  const {api, escapeHtml:esc, navigate} = Workspace;
  // 统一「无后端时显式降级」（裁决第 3 项）：不得静默吞掉未接入错误。
  const NOT_INTEGRATED_TEXT = '未接入（未纳入当前切片）';
  const SERVICE_UNAVAILABLE_TEXT = '服务暂不可用';
  function degradationText(error, fallback){
    const shared = window.GWDegradation;
    if(shared && typeof shared.isNotIntegrated === 'function'){
      if(shared.isNotIntegrated(error)) return shared.NOT_INTEGRATED_MESSAGE || NOT_INTEGRATED_TEXT;
      if(shared.isServiceUnavailable(error)) return shared.SERVICE_UNAVAILABLE_MESSAGE || SERVICE_UNAVAILABLE_TEXT;
    }
    if(error && error.code === 'NOT_INTEGRATED') return NOT_INTEGRATED_TEXT;
    if(error && error.code === 'SERVICE_UNAVAILABLE') return SERVICE_UNAVAILABLE_TEXT;
    return fallback;
  }
  const q = selector => document.querySelector(selector);
  const key = 'workspace_preferences';
  const promptSourceKey = 'prompt_source_settings_v1';
  const contextPreferences = {
    contextToolbarEnabled: 'gw_context_toolbar_enabled',
    contextTreeEnabled: 'gw_context_tree_enabled',
  };
  function readContextPreference(key) {
    const value = String(localStorage.getItem(key) || '').toLowerCase();
    return !['0', 'false', 'off', 'disabled'].includes(value);
  }
  function initContextPreferences() {
    Object.entries(contextPreferences).forEach(([id, storageKey]) => {
      const checkbox = document.getElementById(id);
      if (!checkbox) return;
      try { checkbox.checked = readContextPreference(storageKey); }
      catch (_) { checkbox.disabled = true; q('#contextPreferenceState').textContent = '浏览器禁止本地存储，无法修改显示偏好。'; }
      checkbox.addEventListener('change', () => {
        try {
          localStorage.setItem(storageKey, checkbox.checked ? '1' : '0');
        } catch (_) {
          checkbox.checked = !checkbox.checked;
          q('#contextPreferenceState').textContent = '保存失败，请检查浏览器本地存储权限。';
          return;
        }
        q('#contextPreferenceState').textContent = '显示偏好已保存到此浏览器。';
        if (window.parent !== window) window.parent.postMessage({type: 'gw-context-preference-change'}, window.location.origin);
      });
    });
  }
  let preferences = {};
  let promptSourceManifest = null;
  try { preferences = JSON.parse(localStorage.getItem(key) || '{}'); } catch(e) {}
  function save(){localStorage.setItem(key,JSON.stringify(preferences));q('#saveState').textContent='SAVED';setTimeout(()=>q('#saveState').textContent='LOCAL SETTINGS',900);}
  function readPromptSourceSettings(){
    try {
      const value = JSON.parse(localStorage.getItem(promptSourceKey) || '{}');
      return value && typeof value === 'object' ? value : {};
    } catch(e) { return {}; }
  }
  function writePromptSourceSettings(settings){
    localStorage.setItem(promptSourceKey, JSON.stringify({enabled:settings.enabled || {}, snapshotId:promptSourceManifest?.snapshotId || '', updatedAt:new Date().toISOString()}));
  }
  function promptSourceIsEnabled(sourceId){ return readPromptSourceSettings().enabled?.[sourceId] !== false; }
  function setPromptSourceState(message){ const state=q('#promptSourceSnapshotState'); if(state) state.textContent=message; }
  function renderPromptSources(){
    const list=q('#promptSourceList');
    if(!list || !promptSourceManifest) return;
    const settings=readPromptSourceSettings();
    const sources=Array.isArray(promptSourceManifest.sources) ? promptSourceManifest.sources : [];
    list.innerHTML=sources.map(source=>{
      const id=String(source.id || '');
      const enabled=promptSourceIsEnabled(id);
      const sourcePath=String(source.path || '').replace(/^\/+/, '');
      return `<article class="prompt-source-row ${enabled ? '' : 'is-disabled'}">
        <div class="prompt-source-toggle"><button type="button" class="toggle ${enabled ? 'on' : ''}" role="switch" aria-checked="${enabled}" aria-label="${enabled ? '停用来源' : '启用来源'}" data-prompt-source-toggle="${esc(id)}" title="${enabled ? '停用来源' : '启用来源'}"><i></i></button></div>
        <div class="prompt-source-main"><div class="prompt-source-title"><strong>${esc(source.name || id)}</strong><span class="prompt-source-badge">本地</span></div><a href="${esc(source.homepage || '#')}" target="_blank" rel="noopener">${esc(source.homepage || '无主页')}</a><div class="prompt-source-meta"><span>${Number(source.count || 0)} 条</span><span>${esc(source.license || '未声明许可证')}</span><span>本地正常</span><span>SHA-256 ${esc(String(source.sha256 || '').slice(0, 12))}…</span></div></div>
        <div class="prompt-source-actions"><details><summary>查看内容</summary><div class="prompt-source-details"><span>快照文件：<code>${esc(sourcePath)}</code></span><span>快照时间：${esc(promptSourceManifest.generatedAt || '—')}</span><span>来源状态：${enabled ? '已启用' : '已停用'}</span></div></details><button type="button" class="btn" data-prompt-source-reload="${esc(id)}">重新读取本地快照</button></div>
      </article>`;
    }).join('') || '<div class="prompt-source-loading">清单中没有可用来源。</div>';
    if(window.lucide) window.lucide.createIcons();
    const snapshot=promptSourceManifest.snapshotId || '固定快照';
    setPromptSourceState(`${snapshot} · ${sources.length} 个来源`);
  }
  async function loadPromptSources(){
    const list=q('#promptSourceList');
    if(!list) return;
    setPromptSourceState('正在读取');
    try {
      const response=await fetch('/static/prompt-registry/manifest.json',{cache:'no-store'});
      if(!response.ok) throw new Error('本地快照清单读取失败');
      promptSourceManifest=await response.json();
      if(!Array.isArray(promptSourceManifest.sources)) throw new Error('本地快照清单格式无效');
      const settings=readPromptSourceSettings();
      const enabled={...(settings.enabled || {})};
      promptSourceManifest.sources.forEach(source=>{ if(!(source.id in enabled)) enabled[source.id]=true; });
      writePromptSourceSettings({enabled});
      renderPromptSources();
    } catch(error) {
      setPromptSourceState('读取失败');
      list.innerHTML=`<div class="prompt-source-loading">${esc(error.message || '本地快照清单读取失败')}</div>`;
    }
  }
  function setSection(section){
    if(['appearance','system'].includes(section)) section='general';
    if(![...document.querySelectorAll('[data-panel]')].some(panel=>panel.dataset.panel===section)) section='general';
    document.querySelectorAll('[data-section]').forEach(button=>button.classList.toggle('active',button.dataset.section===section));
    document.querySelectorAll('[data-panel]').forEach(panel=>panel.classList.toggle('active',panel.dataset.panel===section));
    const params=new URLSearchParams(location.hash.slice(1));params.set('section',section);if(document.documentElement.classList.contains('workspace-embedded'))params.set('embedded','1');history.replaceState(null,'',`${location.pathname}${location.search}#${params.toString()}`);
  }
  function init(){
    initContextPreferences();
    q('#defaultPage').value=preferences.default_page||'index';
    document.querySelectorAll('[data-setting]').forEach(toggle=>{const name=toggle.dataset.setting;const fallback=['task_refresh','restore_page'].includes(name);const on=preferences[name]??fallback;toggle.classList.toggle('on',on);toggle.setAttribute('aria-pressed',String(on));});
    const theme=localStorage.getItem('studio_theme') || localStorage.getItem('canvas_theme') || localStorage.getItem('theme') || 'dark';
    const dark=theme==='dark';q('#themeToggle').classList.toggle('on',dark);q('#themeToggle').setAttribute('aria-pressed',String(dark));
    setSection(new URLSearchParams(location.search).get('section')||new URLSearchParams(location.hash.slice(1)).get('section')||'general');
  }
  document.addEventListener('click',event=>{
    const section=event.target.closest('[data-section]');if(section)setSection(section.dataset.section);
    const toggle=event.target.closest('[data-setting]');if(toggle){const name=toggle.dataset.setting;preferences[name]=!toggle.classList.contains('on');toggle.classList.toggle('on',preferences[name]);toggle.setAttribute('aria-pressed',String(preferences[name]));save();}
    const nav=event.target.closest('[data-nav]');if(nav)navigate(nav.dataset.nav);
    if(event.target.closest('#themeToggle')){try{parent.toggleTheme();}catch(e){};const themeToggle=event.target.closest('#themeToggle');themeToggle.classList.toggle('on');themeToggle.setAttribute('aria-pressed',String(themeToggle.classList.contains('on')));}
    if(event.target.closest('#languageToggle')){try{parent.toggleLanguage();}catch(e){}}
    const sourceToggle=event.target.closest('[data-prompt-source-toggle]');
    if(sourceToggle){
      const settings=readPromptSourceSettings();
      settings.enabled={...(settings.enabled || {}),[sourceToggle.dataset.promptSourceToggle]:!promptSourceIsEnabled(sourceToggle.dataset.promptSourceToggle)};
      writePromptSourceSettings(settings);
      renderPromptSources();
      return;
    }
    const sourceReload=event.target.closest('[data-prompt-source-reload]');
    if(sourceReload){ loadPromptSources(); return; }
  });
  q('#defaultPage').addEventListener('change',event=>{preferences.default_page=event.target.value;save();});

  // 团队偏好载入与保存
  api('/api/asset-registry/preferences/team').then(res => {
    const p = (res && res.preferences) || {};
    if (q('#teamNaming')) q('#teamNaming').value = p.naming_convention || '{project}_{entity}_{version}';
    if (q('#teamVisibility')) q('#teamVisibility').value = p.default_review_visibility || 'team';
  }).catch(error => {
    const label = degradationText(error, '');
    if (!label) return;
    const button = q('#btnSaveTeamPrefs');
    if (button) { button.textContent = label; button.dataset.gwDegradation = error.code === 'SERVICE_UNAVAILABLE' ? 'service_unavailable' : 'not_integrated'; }
  });

  q('#btnSaveTeamPrefs')?.addEventListener('click', async () => {
    const naming = q('#teamNaming')?.value.trim() || '{project}_{entity}_{version}';
    const visibility = q('#teamVisibility')?.value || 'team';
    try {
      q('#btnSaveTeamPrefs').disabled = true;
      q('#btnSaveTeamPrefs').textContent = '保存中…';
      await api('/api/asset-registry/preferences/team', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ naming_convention: naming, default_review_visibility: visibility }),
      });
      q('#btnSaveTeamPrefs').textContent = '已同步';
      setTimeout(() => { q('#btnSaveTeamPrefs').disabled = false; q('#btnSaveTeamPrefs').textContent = '保存同步'; }, 1200);
    } catch (e) {
      alert(`${degradationText(e, '保存失败')}: ${e.message}`);
      q('#btnSaveTeamPrefs').disabled = false;
      q('#btnSaveTeamPrefs').textContent = '保存同步';
    }
  });

  Promise.all([
    api('/api/app-info').then(value => ({ value, error: null })).catch(error => ({ value: null, error })),
    api('/api/asset-registry/status').then(value => ({ value, error: null })).catch(error => ({ value: null, error })),
  ]).then(([appResult, statusResult]) => {
    const app = appResult.value || {};
    const status = statusResult.value || {};
    const appLabel = appResult.error ? degradationText(appResult.error, '未知') : (app.version || '不可用');
    const backendLabel = statusResult.error ? degradationText(statusResult.error, '未知') : (status.backend || '不可用');
    const readyLabel = statusResult.error ? degradationText(statusResult.error, '未知') : (status.ready ? '正常' : '未就绪');
    const degraded = appResult.error || statusResult.error;
    const info = q('#systemInfo');
    if (info) info.dataset.gwDegradation = degraded ? (degraded.code === 'SERVICE_UNAVAILABLE' ? 'service_unavailable' : 'not_integrated') : 'ok';
    q('#systemInfo').innerHTML=`<div class="summary-stat"><span>工作台版本</span><strong style="font-size:17px">${esc(appLabel)}</strong></div><div class="summary-stat"><span>资产后端</span><strong style="font-size:17px">${esc(backendLabel)}</strong></div><div class="summary-stat"><span>注册表状态</span><strong style="font-size:17px">${esc(readyLabel)}</strong></div><button class="btn" id="checkUpdate" style="margin-top:14px">检查更新</button>`;
    q('#checkUpdate')?.addEventListener('click',()=>{try{parent.checkForUpdates(true);}catch(e){}});
  });

  init();
  loadPromptSources();
})();
