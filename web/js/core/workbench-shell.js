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
  const ROUTES = {
    'index.html': 'index.html', 'projects.html': 'projects.html', 'workshop.html': 'workshop.html',
    'production.html': 'production.html', 'storyboard.html': 'storyboard.html?view=canvas',
    'agents.html': 'agents.html', 'assets.html': 'assets.html', 'collab.html': 'collab.html',
    'workflow-workbench.html': 'workflow-workbench.html', 'settings.html': 'settings.html'
  };
  // 部分路由会移除旧的 script 节点，但不会撤销浏览器已经建立的全局绑定。
  // 以稳定指纹去重，避免外部脚本重复副作用和内联 const 重复声明。
  const executedRouteScriptKeys = new Set();
  let workshopRouteBootstrapped = false;
  // popstate 时 location 已经改变，保留实际仍挂载的控制器地址。
  let mountedRouteHref = location.href;
  function routeScriptKey(script, baseURI = document.baseURI) {
    const type = (script.getAttribute('type') || '').trim().toLowerCase();
    const src = script.getAttribute('src');
    if (src) return `${type}|src:${new URL(src, baseURI || location.href).href}`;
    return `${type}|inline:${script.textContent || ''}`;
  }
  function seedInitialRouteScripts() {
    document.querySelectorAll('body > script').forEach(script => {
      if (!script.src || !script.src.includes('/static/js/core/workbench-shell.js')) {
        executedRouteScriptKeys.add(routeScriptKey(script));
      }
    });
    const pageName = location.pathname.split('/').pop() || 'index.html';
    workshopRouteBootstrapped = pageName === 'workshop.html';
  }
  function standardRightDeck() {
    const deck = document.querySelector('.topbar-master-deck .recessed-deck-slot');
    if (!deck || deck.children.length < 2) return;
    const old = deck.lastElementChild;
    if (old?.classList.contains('gw-shell-right')) return;
    const right = document.createElement('div');
    right.className = 'gw-shell-right';
    right.innerHTML = '<div class="gw-shell-fader"><span>FLUX</span><div class="hw-fader-track-horizontal gw-shell-fader-track"><div class="hw-fader-glow-bar" data-gw-gpu-util-fill style="width:0%"></div><div class="hw-fader-thumb-3d" data-gw-gpu-util-thumb style="left:0%"></div></div><span class="text-amber-300" data-gw-gpu-util title="真实数据源：GET /api/observability/health 的 gpu_telemetry（读取中）">读取中…</span></div><div class="gw-shell-fader"><span>VRAM</span><div class="hw-fader-track-horizontal gw-shell-fader-track"><div class="hw-fader-glow-bar" data-gw-gpu-vram-fill style="width:0%;background:linear-gradient(90deg,#38bdf8,#2dd4bf)"></div><div class="hw-fader-thumb-3d" data-gw-gpu-vram-thumb style="left:0%"></div></div><span class="text-amber-300" data-gw-gpu-vram title="真实数据源：GET /api/observability/health 的 gpu_telemetry（读取中）">读取中…</span></div><span class="h-4 w-px bg-white/10"></span><span class="gw-type-body-md font-mono text-[#dfc384]">CF</span><button type="button" class="hw-slide-toggle" data-gw-comfy-toggle aria-pressed="false" aria-label="切换 CF 后台控制"><div class="hw-slide-peg"></div></button><span class="h-4 w-px bg-white/10"></span><a id="topbarUnifiedTrashBtn" class="gw-shell-trash" href="projects.html?openTrash=1" onclick="if(window.WorkbenchProjects?.openGlobalTrashDrawer){event.preventDefault();window.WorkbenchProjects.openGlobalTrashDrawer();}" title="统一回收站" aria-label="打开统一回收站"><span class="hw-mini-knob"><span class="hw-mini-knob-arc" style="border-top-color:#ef4444;border-right-color:#f59e0b"></span><span class="hw-mini-knob-inner"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></span></span><span id="topbarTrashBadge" class="gw-shell-trash-badge" hidden>0</span></a><div class="h-4 w-px bg-white/10"></div><div class="hw-avatar-keycap" title="认证状态未接入：点击打开认证中心查看真实登录状态" role="button" tabindex="0" aria-label="打开认证中心" data-gw-identity="unverified"><div class="hw-avatar-keycap-inner"><i data-lucide="shield-check" class="w-4 h-4 text-[#eddab3]"></i></div><span class="hw-avatar-keycap-status"></span></div>';
    old.replaceWith(right);
    // 顶栏推子是后注入的：用最近一次真实 GPU 读数立即回放，
    // 否则会一直停在「读取中」直到 15s 周期刷新（fake-degradation 空窗）。
    try {
      if (window.HardwareDeck && typeof window.HardwareDeck.applyGpuTelemetry === 'function') {
        window.HardwareDeck.applyGpuTelemetry(window.HardwareDeck.lastGpuCheck || null);
      }
    } catch (e) {}
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }
  function injectTrash() {
    const deck = document.querySelector('.topbar-master-deck .recessed-deck-slot');
    if (!deck || deck.querySelector('.gw-shell-trash') || document.querySelector('#topbarUnifiedTrashBtn')) return;
    const right = deck.lastElementChild;
    if (!right) return;
    const button = document.createElement('a');
    button.className = 'gw-shell-trash';
    button.href = 'projects.html?openTrash=1';
    button.title = '统一回收站';
    button.setAttribute('aria-label', '打开统一回收站');
    button.innerHTML = '<span class="hw-mini-knob"><span class="hw-mini-knob-arc" style="border-top-color:#ef4444;border-right-color:#f59e0b"></span><span class="hw-mini-knob-inner"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></span></span><span class="gw-shell-trash-badge" hidden>0</span>';
    const divider = document.createElement('span');
    divider.className = 'h-4 w-px bg-white/10';
    right.insertBefore(divider, right.firstChild);
    right.insertBefore(button, right.firstChild);
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
  }
  function syncNavPills(href = location.href) {
    const url = new URL(href, location.href);
    const activeName = url.pathname.split('/').pop() || 'index.html';
    const settingsActive = activeName === 'settings.html' || (activeName === 'index.html' && url.searchParams.get('view') === 'settings');
    document.querySelectorAll('.nav-pill-btn').forEach(item => {
      const itemHref = item.getAttribute('href') || '';
      let active = false;
      if (item.id === 'navPillSettings') active = settingsActive;
      else if (item.id === 'navPillDashboard') active = !settingsActive && activeName === 'index.html';
      else if (itemHref) {
        const target = new URL(itemHref, location.href);
        const targetName = target.pathname.split('/').pop() || 'index.html';
        active = targetName === activeName;
        if (targetName === 'index.html' && target.searchParams.get('view') === 'settings') active = settingsActive;
      }
      item.classList.toggle('pill-capsule-active', active);
      item.classList.toggle('pill-capsule-inactive', !active);
      if (item.matches('button')) item.setAttribute('aria-pressed', String(active));
    });
  }

  let comfyBusy = false;
  let comfyPollTimer = null;
  function notifyComfy(message, isError) {
    if (window.showWorkbenchToast) { window.showWorkbenchToast(message, isError); return; }
    if (window.showToast) { window.showToast(message, isError); return; }
    let notice = document.getElementById('gwComfyNotice');
    if (!notice) { notice = document.createElement('div'); notice.id = 'gwComfyNotice'; notice.setAttribute('role','alert'); notice.style.cssText='position:fixed;top:90px;right:20px;z-index:9999;background:#151515;color:#dfc384;padding:12px;border:1px solid #dfc384'; document.body.appendChild(notice); }
    notice.textContent = message;
    notice.hidden = false;
    clearTimeout(notice._timer);
    notice._timer = setTimeout(() => { notice.hidden = true; }, 5000);
  }
  function authReady() { return Boolean(window.GWAuthGate?.isAuthenticated()); }
  function renderComfyState(data) {
    const online = data.status === 'online' && data.highlight === true;
    document.querySelectorAll('[data-gw-comfy-toggle]').forEach(toggle => {
      toggle.classList.toggle('active', online);
      toggle.setAttribute('aria-pressed', String(online));
      // 权限由点击反馈和服务端检查处理；不要用 disabled 吞掉交互。
      toggle.disabled = false;
      toggle.setAttribute('aria-busy', String(comfyBusy));
      toggle.dataset.gwComfyStatus = data.status || 'unknown';
      toggle.title = data.status === 'unknown' ? 'CF 状态未知' : online ? 'CF 运行中，点击关闭' : 'CF 未运行，点击后台启动';
      if (!authReady()) toggle.title = '请先登录后读取/控制 CF，点击头像打开认证中心';
      else if (window.HardwareDeck?.authState?.principal?.role !== 'admin') toggle.title = 'CF 状态只读；启停仅限管理员';
      const label = toggle.previousElementSibling;
      if (label && label.textContent.trim() === 'CF') label.classList.add('text-[#dfc384]');
    });
  }
  async function readComfyState() {
    if (!authReady()) {
      const error = new Error('请先登录后读取 CF 状态'); error.status = 401; throw error;
    }
    const authRevision = window.GWAuthGate?.revision;
    const response = await fetch('/api/god_workflow/comfy-control', {credentials:'same-origin'});
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) window.GWAuthGate?.invalidate(401, authRevision, '/api/god_workflow/comfy-control');
    if (!response.ok) { const error = new Error(data.detail?.message || `CF 状态读取失败（HTTP ${response.status}）`); error.status = response.status; throw error; }
    return data;
  }
  async function pollComfyState() {
    if (comfyBusy || !authReady() || window.GWAuthGate?.paused?.has('/api/god_workflow/comfy-control')) {
      if (!authReady()) renderComfyState({status:'unknown', highlight:false});
      return;
    }
    comfyBusy = true;
    try { renderComfyState(await readComfyState()); }
    catch (error) {
      renderComfyState({status:'unknown', highlight:false});
      document.querySelectorAll('[data-gw-comfy-toggle]').forEach(t => { t.title = `CF 状态未知：${error.message || '读取失败'}；点击重试控制`; });
    }
    finally { comfyBusy = false; document.querySelectorAll('[data-gw-comfy-toggle]').forEach(t => { t.disabled = false; t.setAttribute('aria-busy', 'false'); }); }
  }
  async function comfyControl(toggle) {
    if (!authReady()) {
      if (!authReady()) { notifyComfy('请先登录后控制 CF', true); window.HardwareDeck?.openAccountModal?.(); }
      return;
    }
    if (window.HardwareDeck?.authState?.principal?.role !== 'admin') { notifyComfy('CF 启停仅限管理员', true); return; }
    if (comfyBusy) { notifyComfy('CF 正在读取状态或执行控制，请稍后重试', false); return; }
    window.GWAuthGate?.paused?.delete('/api/god_workflow/comfy-control');
    comfyBusy = true;
    document.querySelectorAll('[data-gw-comfy-toggle]').forEach(t => { t.disabled = false; t.setAttribute('aria-busy', 'true'); });
    try {
      // 点击前重新读服务端，不能以按钮残留样式决定启动/停止。
      const current = await readComfyState();
      const action = current.highlight ? 'stop' : 'start';
      let confirmation_token;
      if (action === 'stop' && !current.managed) {
        if (!current.can_stop || !current.confirmation_token) throw new Error('CF 进程身份未知，拒绝关闭');
        if (!window.confirm('CF 由外部启动。确认关闭已识别的本机 CF 实例？这将中断该实例正在执行的任务。')) return;
        confirmation_token = current.confirmation_token;
      }
      const authRevision = window.GWAuthGate?.revision;
      const response = await fetch('/api/god_workflow/comfy-control', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, confirmation_token})});
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) window.GWAuthGate?.invalidate(401, authRevision, '/api/god_workflow/comfy-control');
      if (data.status) renderComfyState(data);
      if (!response.ok || !data.ok) throw new Error(data.detail?.message || data.code || 'CF 控制失败');
      notifyComfy(action === 'stop' ? 'CF 已确认关闭' : 'CF 已确认运行', false);
    } catch (error) {
      notifyComfy(error.message || 'CF 控制失败', true);
    } finally {
      comfyBusy = false;
      await pollComfyState();
    }
  }
  function bindComfyControl() {
    document.querySelectorAll('[data-gw-comfy-toggle]').forEach(toggle => {
      if (toggle.dataset.gwBound) return; toggle.dataset.gwBound = '1';
      toggle.addEventListener('click', () => comfyControl(toggle));
    });
    if (!comfyPollTimer) comfyPollTimer = setInterval(pollComfyState, 5000);
    pollComfyState();
    if (!window.__gwComfyAuthBound) {
      window.__gwComfyAuthBound = true;
      window.addEventListener('gw-auth-state', () => { bindComfyControl(); pollComfyState(); });
    }
  }
  function init() {
    seedInitialRouteScripts();
    standardRightDeck();
    bindComfyControl();
    injectTrash();
    syncNavPills(location.href);
  }
  function shellWorkspace() { return document.querySelector('.topbar-master-deck')?.nextElementSibling || null; }
  function targetWorkspace(doc) { return doc.querySelector('.topbar-master-deck')?.nextElementSibling || doc.body; }
  function firstSidebar(workspace) { return workspace?.querySelector(':scope > aside') || null; }
  function workspaceNodes(workspace, aside) { return [...(workspace?.children || [])].filter(node => node !== aside); }
  function syncRouteDeck(doc) {
    const currentHeader = document.querySelector('.topbar-master-deck');
    const nextHeader = doc.querySelector('.topbar-master-deck');
    const currentDeck = currentHeader?.querySelector(':scope > .recessed-deck-slot');
    const nextDeck = nextHeader?.querySelector(':scope > .recessed-deck-slot');
    if (!currentHeader || !nextHeader || !currentDeck || !nextDeck) return;

    // 顶栏上层同样包含页级元素（例如工程标题、首页专用渐变定义）。
    // 只替换上层和下层内容，保留 header 外壳，确保路由后的 deck 与整页加载一致。
    const currentUpper = [...currentHeader.children].find(node =>
      node !== currentDeck && !node.classList.contains('absolute')
    );
    const nextUpper = [...nextHeader.children].find(node =>
      node !== nextDeck && !node.classList.contains('absolute')
    );
    if (currentUpper && nextUpper) {
      currentUpper.replaceWith(document.importNode(nextUpper, true));
    }
    currentDeck.replaceWith(document.importNode(nextDeck, true));
  }
  function syncRouteBody(doc) {
    if (!doc.body) return;
    document.body.className = doc.body.className;
    [...document.body.attributes].forEach(attribute => {
      if (attribute.name.startsWith('data-')) document.body.removeAttribute(attribute.name);
    });
    [...doc.body.attributes].forEach(attribute => {
      if (attribute.name.startsWith('data-')) document.body.setAttribute(attribute.name, attribute.value);
    });
  }
  async function runRouteScripts(doc) {
    document.querySelectorAll('script[data-gw-route-script]').forEach(script => script.remove());
    const scripts = [...doc.querySelectorAll('body > script')].filter(script => {
      const src = script.getAttribute('src') || '';
      return (src && !src.includes('workbench-shell.js') && src.includes('/static/')) || (!src && script.textContent.trim());
    });
    for (const source of scripts) {
      const key = routeScriptKey(source, doc.baseURI || location.href);
      if (executedRouteScriptKeys.has(key)) continue;
      const script = document.createElement('script');
      [...source.attributes].forEach(attribute => {
        if (attribute.name !== 'src') script.setAttribute(attribute.name, attribute.value);
      });
      script.dataset.gwRouteScript = '1';
      if (source.getAttribute('src')) {
        script.src = new URL(source.getAttribute('src'), doc.baseURI || location.href).href;
        script.async = false;
        const loaded = await new Promise(resolve => {
          script.onload = () => resolve(true);
          script.onerror = () => resolve(false);
          document.body.appendChild(script);
        });
        if (loaded) executedRouteScriptKeys.add(key);
      } else {
        script.textContent = source.textContent;
        document.body.appendChild(script);
        executedRouteScriptKeys.add(key);
      }
    }
  }
  function disposeRouteController(href, nextHref = href) {
    const pageName = new URL(href, location.href).pathname.split('/').pop() || 'index.html';
    const nextPageName = new URL(nextHref, location.href).pathname.split('/').pop() || 'index.html';
    if (pageName === 'workflow-workbench.html') {
      window.WorkbenchWorkflow?.dispose?.();
    }
    if (pageName === nextPageName) return;
    if (pageName === 'workshop.html' || pageName === 'collab.html') {
      try {
        if (pageName === 'workshop.html') window.WorkbenchWorkshop?.dispose?.();
        if (pageName === 'collab.html') window.WorkbenchCollab?.dispose?.();
      } catch (error) {
        console.warn('[GW shell] route controller dispose failed', pageName, error);
      }
    }
  }

  function refreshRouteController(href) {
    const pageName = new URL(href, location.href).pathname.split('/').pop() || 'index.html';
    const rebinders = {
      'index.html': window.WorkbenchHome?.rebind,
      'projects.html': window.WorkbenchProjects?.rebind,
      'production.html': window.WorkbenchProduction?.rebind,
      'workshop.html': window.WorkbenchWorkshop?.rebind,
      'storyboard.html': window.WorkbenchStoryboard?.rebind,
      'agents.html': window.WorkbenchAgents?.rebind,
      'assets.html': window.WorkbenchAssets?.rebind,
      'collab.html': window.WorkbenchCollab?.rebind,
      'workflow-workbench.html': window.WorkbenchWorkflow?.rebind,
    };
    try { rebinders[pageName]?.(); } catch (error) {
      console.warn('[GW shell] route controller rebind failed', pageName, error);
    }
    if (pageName === 'production.html') {
      window.WorkbenchProduction?.initRouting?.();
      window.WorkbenchProduction?.renderSceneCatalog?.();
      window.WorkbenchProduction?.renderShots?.();
    }
    if (pageName === 'workshop.html') {
      const title = document.getElementById('workshopProjectTitle');
      const projectName = localStorage.getItem('workspace_project_name');
      if (title && projectName) title.textContent = `${projectName} · 影视工坊流水线`;
    }
  }

  async function navigate(href, push) {
    const url = new URL(href, location.href);
    if (url.origin !== location.origin) return;
    const currentWorkspace = shellWorkspace();
    const currentSidebar = firstSidebar(currentWorkspace);
    if (!currentWorkspace) { location.href = url.href; return; }
    try {
      const response = await fetch(url.href, { credentials: 'same-origin', headers: { 'X-GW-Partial': '1' } });
      if (!response.ok) throw new Error('route ' + response.status);
      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const nextWorkspace = targetWorkspace(doc);
      syncRouteBody(doc);
      document.querySelectorAll('style[data-gw-route-style],link[data-gw-route-style]').forEach(node => node.remove());
    const routeStyles = [...doc.head.querySelectorAll('style,link[rel="stylesheet"]')];
      for (const node of routeStyles) {
        if (node.tagName === 'SCRIPT') continue;
        const copy = node.cloneNode(true); copy.dataset.gwRouteStyle = '1'; document.head.appendChild(copy);
      }
      // 页面使用 Tailwind utility class 时，强制刷新不会经过壳层的脚本
      // 重放路径；直接导航必须让本地 Tailwind 编译器在页面自己的 head 中运行。
      const tailwindScript = [...doc.head.querySelectorAll('script[src*="tailwindcss-cdn.js"]')][0];
      if (tailwindScript && !document.querySelector('script[data-gw-tailwind-route]')) {
        const copy = document.createElement('script');
        copy.src = new URL(tailwindScript.getAttribute('src'), doc.baseURI || location.href).href;
        copy.dataset.gwTailwindRoute = '1';
        document.head.appendChild(copy);
      }
      // Settings uses a direct <main> root while work pages use a workspace
      // wrapper. Preserve the shared header, but swap the complete content
      // root at this boundary so the target layout classes remain intact.
      if (currentWorkspace.matches('main') !== nextWorkspace.matches('main')) {
        disposeRouteController(mountedRouteHref, url.href);
        mountedRouteHref = url.href;
        currentWorkspace.replaceWith(document.importNode(nextWorkspace, true));
        syncRouteDeck(doc);
        if (push) history.pushState({}, '', url.href);
        if (doc.title) document.title = doc.title;
        standardRightDeck();
        bindComfyControl();
        await runRouteScripts(doc);
       refreshRouteController(url.href);
        syncNavPills(url.href);
        window.dispatchEvent(new CustomEvent('gw:route-loaded', { detail: { href: url.href } }));
        if (window.lucide?.createIcons) window.lucide.createIcons();
        return;
      }
      const nextSidebar = firstSidebar(nextWorkspace);
      const nextNodes = workspaceNodes(nextWorkspace, nextSidebar);
      if (!nextNodes.length) throw new Error('target workspace missing');
      syncRouteDeck(doc);
      disposeRouteController(mountedRouteHref, url.href);
      mountedRouteHref = url.href;
      if (currentSidebar && nextSidebar) {
        currentSidebar.innerHTML = nextSidebar.innerHTML;
        currentSidebar.id = nextSidebar.id;
        currentSidebar.className = nextSidebar.className;
      } else if (currentSidebar && !nextSidebar) {
        currentSidebar.remove();
      }
      workspaceNodes(currentWorkspace, currentSidebar).forEach(node => node.remove());
      if (!currentSidebar && nextSidebar) currentWorkspace.appendChild(document.importNode(nextSidebar, true));
      nextNodes.forEach(node => currentWorkspace.appendChild(document.importNode(node, true)));
      if (push) history.pushState({}, '', url.href);
      if (doc.title) document.title = doc.title;
      syncNavPills(url.href);
      standardRightDeck();
      bindComfyControl();
      await runRouteScripts(doc);
       refreshRouteController(url.href);
      window.dispatchEvent(new CustomEvent('gw:route-loaded', { detail: { href: url.href } }));
      if (window.lucide?.createIcons) window.lucide.createIcons();
    } catch (error) {
      console.warn('[GW shell] partial route failed, falling back to full navigation', error);
      location.href = url.href;
    }
  }
  function bindNavigation() {
    document.addEventListener('click', event => {
      const link = event.target.closest('.nav-pill-btn[href], .tree-node-card[href]');
      if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (link.matches('.tree-node-card') && !firstSidebar(shellWorkspace())?.contains(link)) return;
      const rawHref = link.getAttribute('href') || '';
      const targetUrl = new URL(rawHref, location.href);
      if (targetUrl.origin !== location.origin || !targetUrl.pathname.startsWith('/static/pages/') || !ROUTES[targetUrl.pathname.split('/').pop()] || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
      event.preventDefault();
      const target = targetUrl.pathname.endsWith('/index.html') && targetUrl.searchParams.get('view') === 'settings' ? 'settings.html' : link.href;
      navigate(target, true);
    });
    window.addEventListener('popstate', () => navigate(location.href, false));
  }
  function bindSelection() {
    const selector = '.tree-node-card';
    let observer;
    function prepare() {
      const aside = firstSidebar(shellWorkspace());
      if (!aside) return;
      aside.querySelectorAll(selector).forEach(item => {
        if (!item.matches('a, button')) {
          item.setAttribute('role', 'button');
          item.tabIndex = 0;
        }
        if (!item.matches('a')) {
          const pressed = String(item.classList.contains('active'));
          if (item.getAttribute('aria-pressed') !== pressed) item.setAttribute('aria-pressed', pressed);
        }
      });
    }
    function observeSidebar() {
      observer?.disconnect();
      prepare();
      const aside = firstSidebar(shellWorkspace());
      if (!aside) return;
      observer = new MutationObserver(prepare);
      observer.observe(aside, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    }
    document.addEventListener('click', event => {
      const target = event.target.closest(selector);
      const aside = firstSidebar(shellWorkspace());
      if (!target || !aside?.contains(target) || target.matches('[disabled], [aria-disabled="true"]') || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      aside.querySelectorAll(selector).forEach(item => {
        item.classList.toggle('active', item === target);
      });
    }, true);
    document.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const target = event.target.closest(selector);
      if (!target || event.target !== target || target.matches('a, button') || !firstSidebar(shellWorkspace())?.contains(target)) return;
      event.preventDefault();
      target.click();
    });
    window.addEventListener('gw:route-loaded', observeSidebar);
    observeSidebar();
  }
  const oldInit = init;
  init = function () { oldInit(); bindNavigation(); bindSelection(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
