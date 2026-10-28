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

/**
 * Browser event contract:
 * - Invalid URL syntax emits `gw:omnibar-error` with `INVALID_RESULT_URL`.
 * - Same-origin navigation failures emit `NAVIGATION_FAILED` separately.
 * - Cross-origin destinations and non-HTTP(S) protocols emit
 *   `CROSS_ORIGIN_URL_BLOCKED`; command activation failures emit
 *   `COMMAND_ACTIVATION_FAILED`; command activation timeouts emit
 *   `COMMAND_ACTIVATION_TIMEOUT`. Both use static, sanitized UI messages and
 *   never expose the original exception.
 * - The Omnibar displays an inline alert and stays open on these errors.
 *   Successful same-origin navigation and command activation close it.
 */
(function omnibar(global, document) {
  'use strict';

  const DEFAULT_COMMAND_TIMEOUT_MS = 10000;
  const SEARCH_HINT = /[?？]|^(请|帮我|如何|怎么|为什么|解释|分析|总结|生成|改写)/;
  const RESULT_SELECTOR = '[role="option"]';
  // MVP1-T6：素材模式直接消费镜头级检索 API，命中项带 asset_id + start_sec，
  // 由既有「选中结果」事件驱动素材页把播放器跳到命中时间戳。
  const ASSET_SHOT_SEARCH_API = '/api/asset-registry/search/shots';
  const ASSET_SEARCH_LIMIT = 12;
  const ASSET_SEARCH_DEBOUNCE_MS = 160;
  const MODE_LABELS = [['search', '检索'], ['agent', 'Agent'], ['asset', '素材']];
  const MODE_PLACEHOLDER = {
    search: '搜索项目、画布、资产与任务…',
    agent: '向 Agent 提问，或输入 / 开始对话…',
    asset: '搜索素材镜头，Enter 跳到命中时间戳…',
  };
  const MODE_EMPTY = {
    search: '输入关键词以搜索可访问内容。',
    agent: 'Agent 对话入口已就绪。输入问题后按 Enter 发送。',
    asset: '输入关键词（如「海边 日落」）检索素材镜头。',
  };
  const boundDocuments = new Set();
  const boundFrames = new WeakSet();
  const frameDocuments = new Map();
  let overlay = null;
  let panel = null;
  let input = null;
  let closeButton = null;
  let modeButtons = new Map();
  let resultList = null;
  let emptyState = null;
  let errorState = null;
  let results = [];
  let selectedIndex = -1;
  let mode = 'search';
  let isOpen = false;
  let returnFocus = null;
  let frameObserver = null;
  let initialized = false;
  let activeCommandTimer = null;
  let assetSearchTimer = null;
  let assetSearchSeq = 0;

  function safeClearTimeout(timerId) {
    if (timerId === null || timerId === undefined) return;
    if (typeof global.clearTimeout === 'function') {
      global.clearTimeout(timerId);
    } else if (typeof clearTimeout === 'function') {
      clearTimeout(timerId);
    }
  }

  function safeSetTimeout(callback, delayMs) {
    if (typeof global.setTimeout === 'function') {
      return global.setTimeout(callback, delayMs);
    }
    if (typeof setTimeout === 'function') {
      return setTimeout(callback, delayMs);
    }
    return null;
  }

  function makeElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function makeButton(label, className, ariaLabel) {
    const button = makeElement('button', className, label);
    button.type = 'button';
    button.setAttribute('type', 'button');
    if (ariaLabel) button.setAttribute('aria-label', ariaLabel);
    return button;
  }

  function setMode(nextMode, {manual = false} = {}) {
    if (nextMode !== 'search' && nextMode !== 'agent' && nextMode !== 'asset') return false;
    mode = nextMode;
    if (panel) panel.dataset.mode = mode;
    modeButtons.forEach((button, name) => {
      const active = name === mode;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('is-active', active);
    });
    if (input) {
      input.placeholder = MODE_PLACEHOLDER[mode];
      input.setAttribute('aria-label', mode === 'agent' ? '向 Agent 提问' : mode === 'asset' ? '搜索素材镜头' : '搜索项目、画布、资产与任务');
    }
    if (emptyState) {
      emptyState.textContent = MODE_EMPTY[mode];
    }
    if (manual) panel?.setAttribute('data-mode-selection', 'manual');
    if (mode === 'asset') {
      // 切到素材模式时清掉其他模式的旧结果，避免把项目/任务结果当成镜头结果打开。
      if (manual) setResults([]);
    } else {
      // 离开素材模式即作废仍在途的检索，防止迟到响应覆盖当前列表。
      safeClearTimeout(assetSearchTimer);
      assetSearchTimer = null;
      assetSearchSeq += 1;
    }
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-mode-change', {detail: {mode, manual}}));
    return true;
  }

  function renderResults() {
    if (!resultList || !emptyState) return;
    resultList.replaceChildren();
    results.forEach((result, index) => {
      const option = makeElement('li', 'gw-omnibar-result');
      option.id = `gwOmnibarResult${index}`;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(index === selectedIndex));
      option.dataset.resultIndex = String(index);
      const title = makeElement('span', 'gw-omnibar-result-title', String(result?.title || result?.label || '未命名结果'));
      const description = makeElement('span', 'gw-omnibar-result-description', String(result?.description || result?.subtitle || ''));
      option.append(title, description);
      option.addEventListener('mousemove', () => selectIndex(index));
      option.addEventListener('click', () => activateResult(index).catch(reportActivationFailure));
      resultList.appendChild(option);
    });
    emptyState.hidden = results.length > 0;
    if (input) {
      if (selectedIndex >= 0 && selectedIndex < results.length) input.setAttribute('aria-activedescendant', `gwOmnibarResult${selectedIndex}`);
      else input.removeAttribute('aria-activedescendant');
    }
  }

  function selectIndex(index) {
    if (!results.length) return false;
    selectedIndex = (index + results.length) % results.length;
    resultList?.querySelectorAll(RESULT_SELECTOR).forEach((option, optionIndex) => {
      option.setAttribute('aria-selected', String(optionIndex === selectedIndex));
    });
    input?.setAttribute('aria-activedescendant', `gwOmnibarResult${selectedIndex}`);
    const selected = resultList?.querySelector(`#gwOmnibarResult${selectedIndex}`);
    selected?.scrollIntoView?.({block: 'nearest'});
    return true;
  }

  function setResults(nextResults) {
    results = Array.isArray(nextResults) ? [...nextResults] : [];
    selectedIndex = -1;
    clearError();
    renderResults();
    return results.length;
  }

  function clearError() {
    if (!errorState) return;
    errorState.hidden = true;
    errorState.textContent = '';
  }

  function handleOmnibarError(event) {
    if (!errorState) return;
    let message;
    switch (event?.detail?.code) {
      case 'INVALID_RESULT_URL':
        message = '无法打开该结果链接，请重新搜索或关闭面板。';
        break;
      case 'NAVIGATION_FAILED':
        message = '无法打开该结果链接，请重新搜索或关闭面板。';
        break;
      case 'CROSS_ORIGIN_URL_BLOCKED':
        message = '不支持跳转到外部地址';
        break;
      case 'COMMAND_ACTIVATION_FAILED':
        message = '命令执行失败，请重试。';
        break;
      case 'COMMAND_ACTIVATION_TIMEOUT':
        message = '命令执行超时，请重试。';
        break;
      case 'ASSET_SEARCH_FAILED':
        message = '素材检索失败，请稍后重试。';
        break;
      case 'SEARCH_INDEX_NOT_READY':
        message = '素材检索索引尚未建立，请先在素材页重建索引。';
        break;
      default:
        return;
    }
    errorState.textContent = message;
    errorState.hidden = false;
  }

  function reportActivationFailure(error) {
    console.error(error);
  }

  async function activateResult(index = selectedIndex) {
    const result = results[index];
    if (!result) return false;
    selectedIndex = index;
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-result-select', {detail: {result, index}}));
    if (mode === 'asset') relayAssetSelection(result);
    if (typeof result.activate === 'function') {
      const timeoutMs = Number(result?.timeout) > 0 ? Number(result.timeout) : DEFAULT_COMMAND_TIMEOUT_MS;
      safeClearTimeout(activeCommandTimer);
      activeCommandTimer = null;
      try {
        let timer = null;
        const actionPromise = Promise.resolve(result.activate(result));
        actionPromise.catch(reportActivationFailure);
        const timeoutPromise = new Promise((_, reject) => {
          timer = safeSetTimeout(() => {
            const error = new Error('Command activation timed out');
            error.code = 'COMMAND_ACTIVATION_TIMEOUT';
            reject(error);
          }, timeoutMs);
          activeCommandTimer = timer;
        });
        await Promise.race([actionPromise, timeoutPromise]);
      } catch (err) {
        if (!isOpen) return false;
        const isTimeout = err?.code === 'COMMAND_ACTIVATION_TIMEOUT';
        global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-error', {
          detail: {
            code: isTimeout ? 'COMMAND_ACTIVATION_TIMEOUT' : 'COMMAND_ACTIVATION_FAILED',
            message: isTimeout ? '命令执行超时，请重试。' : '命令执行失败，请重试。',
          },
        }));
        input?.focus({preventScroll: true});
        return true;
      } finally {
        safeClearTimeout(activeCommandTimer);
        activeCommandTimer = null;
      }
    } else if (typeof result.href === 'string') {
      let destination;
      try {
        destination = new URL(result.href, global.location.href);
      } catch (error) {
        global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-error', {
          detail: {
            code: 'INVALID_RESULT_URL',
            href: result.href,
            message: String(error?.message || error),
          },
        }));
        return true;
      }
      const isHttpProtocol = destination.protocol === 'http:' || destination.protocol === 'https:';
      if (destination.origin !== global.location.origin || !isHttpProtocol) {
        global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-error', {
          detail: {
            code: 'CROSS_ORIGIN_URL_BLOCKED',
            message: '不支持跳转到外部地址',
          },
        }));
        return true;
      }
      try {
        global.location.assign(destination.href);
      } catch (error) {
        void error;
        global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-error', {
          detail: {
            code: 'NAVIGATION_FAILED',
            href: result.href,
            message: '无法打开该结果链接，请重新搜索或关闭面板。',
          },
        }));
        return true;
      }
    }
    close();
    return true;
  }

  function emitQuery() {
    clearError();
    const query = input?.value || '';
    if (mode === 'search' && (query.trim().startsWith('/') || SEARCH_HINT.test(query.trim()))) setMode('agent');
    if (mode === 'asset') scheduleAssetSearch(query);
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-query', {detail: {query, mode}}));
  }

  function formatShotTime(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const minutes = Math.floor(total / 60);
    const remain = total % 60;
    return `${String(minutes).padStart(2, '0')}:${String(remain).padStart(2, '0')}`;
  }

  function mapShotResults(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    return items.map((item, index) => {
      const name = String(item?.name || item?.asset_id || '未命名素材');
      const startSec = Number(item?.start_sec);
      const isShot = item?.granularity === 'shot' && item?.start_sec != null && Number.isFinite(startSec);
      const range = isShot ? `${formatShotTime(startSec)}~${formatShotTime(item?.end_sec)}` : '';
      const tags = Array.isArray(item?.tags) ? item.tags.filter(tag => typeof tag === 'string' && tag) : [];
      const summary = String(item?.summary || '').trim();
      return {
        id: String(item?.asset_id || `asset-${index}`),
        asset_id: String(item?.asset_id || ''),
        granularity: isShot ? 'shot' : 'asset',
        start_sec: isShot ? startSec : null,
        title: range ? `${name} · ${range}` : name,
        description: [range, ...tags, summary].filter(Boolean).join(' · '),
      };
    }).filter(result => result.asset_id);
  }

  function assetSearchError(code) {
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-error', {
      detail: {code, message: '素材检索失败，请稍后重试。'},
    }));
  }

  // 素材页在 shell 的 iframe 里运行，窗口内事件到不了它；复用既有
  // asset-global-search 桥接把命中时间戳一并递给素材页（AC7 时间戳跳转）。
  function assetFrameMatches(frame) {
    const raw = String(frame?.getAttribute?.('src') || frame?.src || '');
    if (!raw) return false;
    try {
      const resolved = new URL(raw, global.location.href);
      return resolved.origin === global.location.origin && resolved.pathname.includes('asset-manager');
    } catch (_) {
      return false;
    }
  }

  function relayAssetSelection(result) {
    const assetId = String(result?.asset_id || '').trim();
    if (!assetId) return false;
    const frames = document.querySelectorAll?.('iframe');
    if (!frames || !frames.length) return false;
    const startSec = Number(result?.start_sec);
    const payload = {
      type: 'asset-global-search',
      asset_id: assetId,
    };
    if (result?.granularity === 'shot' && Number.isFinite(startSec)) payload.start_sec = startSec;
    let relayed = false;
    Array.prototype.forEach.call(frames, frame => {
      if (!assetFrameMatches(frame)) return;
      const target = frame.contentWindow;
      if (!target || typeof target.postMessage !== 'function') return;
      try {
        target.postMessage(payload, global.location.origin);
        relayed = true;
      } catch (_) {
        // 跨源或未就绪的 frame 直接跳过，不影响同页事件路径。
      }
    });
    return relayed;
  }

  function scheduleAssetSearch(rawQuery) {
    const query = String(rawQuery || '').trim();
    assetSearchSeq += 1;
    const seq = assetSearchSeq;
    safeClearTimeout(assetSearchTimer);
    assetSearchTimer = null;
    if (!query) {
      setResults([]);
      return;
    }
    assetSearchTimer = safeSetTimeout(() => {
      assetSearchTimer = null;
      void runAssetSearch(query, seq);
    }, ASSET_SEARCH_DEBOUNCE_MS);
  }

  async function runAssetSearch(query, seq) {
    const fetchImpl = typeof global.fetch === 'function' ? global.fetch : null;
    if (!fetchImpl) {
      assetSearchError('ASSET_SEARCH_FAILED');
      return false;
    }
    const params = new URLSearchParams({q: query, limit: String(ASSET_SEARCH_LIMIT)});
    try {
      const response = await fetchImpl(`${ASSET_SHOT_SEARCH_API}?${params.toString()}`, {
        credentials: 'same-origin',
        headers: {'Accept': 'application/json'},
      });
      if (seq !== assetSearchSeq || !isOpen) return false;
      let body = null;
      try {
        body = await response.json();
      } catch (_) {
        body = null;
      }
      if (seq !== assetSearchSeq || !isOpen) return false;
      if (!response.ok) {
        assetSearchError(body?.detail?.code === 'SEARCH_INDEX_NOT_READY'
          ? 'SEARCH_INDEX_NOT_READY'
          : 'ASSET_SEARCH_FAILED');
        return false;
      }
      const mapped = mapShotResults(body);
      setResults(mapped);
      if (emptyState) {
        emptyState.textContent = mapped.length
          ? MODE_EMPTY.asset
          : `未找到与「${query}」匹配的素材镜头。`;
      }
      return true;
    } catch (_) {
      if (seq === assetSearchSeq && isOpen) assetSearchError('ASSET_SEARCH_FAILED');
      return false;
    }
  }

  function emitAgentSubmit() {
    const prompt = String(input?.value || '').trim().replace(/^\//, '').trim();
    if (!prompt) return false;
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-agent-submit', {detail: {prompt}}));
    return true;
  }

  function buildUI() {
    if (overlay) return;
    overlay = makeElement('div', 'gw-omnibar-backdrop');
    overlay.id = 'gwOmnibar';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-hidden', 'true');
    overlay.setAttribute('aria-label', '全站统一命令总线');
    overlay.dataset.floatingSurface = 'omnibar';
    overlay.dataset.floatingKind = 'modal';
    overlay.dataset.floatingDismiss = 'outside';
    overlay.dataset.floatingEscape = 'close-only';

    panel = makeElement('section', 'gw-omnibar-panel');
    panel.id = 'gwOmnibarPanel';
    panel.dataset.mode = mode;
    panel.setAttribute('data-floating-content', 'self');
    panel.setAttribute('aria-label', 'Omnibar 命令面板');

    const header = makeElement('header', 'gw-omnibar-header');
    const titleGroup = makeElement('div', 'gw-omnibar-title-group');
    titleGroup.append(
      makeElement('span', 'gw-omnibar-mark', 'GW'),
      makeElement('span', 'gw-omnibar-title', '统一命令总线'),
      makeElement('span', 'gw-omnibar-status', 'OMNIBAR')
    );
    closeButton = makeButton('×', 'gw-omnibar-close', '关闭命令面板');
    closeButton.dataset.floatingClose = 'omnibar';
    closeButton.addEventListener('click', close);
    header.append(titleGroup, closeButton);

    const modeGroup = makeElement('div', 'gw-omnibar-modes');
    modeGroup.setAttribute('role', 'group');
    modeGroup.setAttribute('aria-label', '命令模式');
    modeButtons = new Map();
    MODE_LABELS.forEach(([name, label]) => {
      const button = makeButton(label, 'gw-omnibar-mode');
      button.id = `gwOmnibarMode${name === 'search' ? 'Search' : name === 'agent' ? 'Agent' : 'Asset'}`;
      button.setAttribute('id', button.id);
      button.dataset.mode = name;
      button.setAttribute('aria-pressed', String(name === mode));
      button.addEventListener('click', () => {
        setMode(name, {manual: true});
        input?.focus({preventScroll: true});
      });
      modeButtons.set(name, button);
      modeGroup.appendChild(button);
    });

    const inputRow = makeElement('div', 'gw-omnibar-input-row');
    input = makeElement('input', 'gw-omnibar-input');
    input.type = 'search';
    input.id = 'gwOmnibarInput';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('type', 'search');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-controls', 'gwOmnibarResults');
    input.addEventListener('input', emitQuery);
    input.addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      if (event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      event.stopPropagation();
      if (mode === 'agent') emitAgentSubmit();
      else activateResult(mode === 'asset' && selectedIndex < 0 && results.length ? 0 : selectedIndex).catch(reportActivationFailure);
    });
    inputRow.append(makeElement('span', 'gw-omnibar-search-icon', '⌕'), input);

    const resultsRegion = makeElement('div', 'gw-omnibar-results-region');
    resultsRegion.setAttribute('aria-live', 'polite');
    resultList = makeElement('ul', 'gw-omnibar-results');
    resultList.id = 'gwOmnibarResults';
    resultList.setAttribute('role', 'listbox');
    resultList.setAttribute('aria-label', '搜索结果');
    emptyState = makeElement('p', 'gw-omnibar-empty', '输入关键词以搜索可访问内容。');
    emptyState.setAttribute('role', 'status');
    errorState = makeElement('p', 'gw-omnibar-error');
    errorState.id = 'gwOmnibarError';
    errorState.setAttribute('id', errorState.id);
    errorState.setAttribute('role', 'alert');
    errorState.setAttribute('aria-atomic', 'true');
    errorState.hidden = true;
    resultsRegion.append(resultList, emptyState, errorState);

    const footer = makeElement('footer', 'gw-omnibar-footer');
    const hint = makeElement('span', 'gw-omnibar-hint', '↑ ↓ 选择');
    hint.append(makeElement('span', 'gw-omnibar-key', 'Enter'), document.createTextNode(' 打开'));
    const escapeHint = makeElement('span', 'gw-omnibar-hint');
    escapeHint.append(makeElement('span', 'gw-omnibar-key', 'ESC'), document.createTextNode(' 关闭'));
    footer.append(hint, escapeHint);

    panel.append(header, modeGroup, inputRow, resultsRegion, footer);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    overlay.addEventListener('click', event => {
      if (event.target !== overlay) return;
      const closed = global.FloatingDismissal?.closeSurface?.(overlay);
      if (!closed) close();
      event.preventDefault();
      event.stopPropagation();
    });
    setMode(mode);
  }

  function connected(element) {
    return Boolean(element && element.isConnected !== false && typeof element.focus === 'function');
  }

  function restoreFocusSoon(target) {
    if (!connected(target)) return;
    const focus = () => {
      if (connected(target)) {
        try {
          target.focus({preventScroll: true});
        } catch (_) {
          // 旧版浏览器不支持 preventScroll 时，仍要把焦点还给触发控件。
          target.focus();
        }
      }
    };
    const raf = global.requestAnimationFrame || (callback => global.setTimeout(callback, 0));
    raf(() => raf(focus));
  }

  function close() {
    safeClearTimeout(activeCommandTimer);
    activeCommandTimer = null;
    safeClearTimeout(assetSearchTimer);
    assetSearchTimer = null;
    assetSearchSeq += 1;
    if (!overlay || overlay.hidden) return false;
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    isOpen = false;
    const focusTarget = returnFocus;
    returnFocus = null;
    global.FloatingDismissal?.scan?.(overlay);
    restoreFocusSoon(focusTarget);
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-close'));
    return true;
  }

  function parentFrameFor(doc) {
    try {
      return doc?.defaultView?.frameElement || null;
    } catch (_) {
      // 跨域文档禁止读取 frameElement；将其视为没有可返回焦点的父 frame。
      return null;
    }
  }

  function open(trigger = document.activeElement, openerDocument = document) {
    if (!overlay) buildUI();
    if (isOpen) {
      input?.focus({preventScroll: true});
      return true;
    }
    const active = openerDocument?.activeElement;
    returnFocus = connected(active) && active !== openerDocument?.body ? active : connected(trigger) ? trigger : document.activeElement;
    const frame = parentFrameFor(openerDocument);
    overlay.hidden = false;
    overlay.setAttribute('aria-hidden', 'false');
    isOpen = true;
    clearError();
    input.value = '';
    setResults([]);
    setMode('search');
    global.FloatingDismissal?.register?.(overlay, {
      kind: 'modal',
      content: panel,
      trigger: frame || (returnFocus?.ownerDocument === document ? returnFocus : null),
      isOpen: () => !overlay.hidden,
      close,
    });
    input.focus({preventScroll: true});
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-open'));
    return true;
  }

  function matchesHotkey(event) {
    if (!event?.ctrlKey || event.shiftKey || event.metaKey || event.altKey) return false;
    const key = String(event.key || '').toLowerCase();
    return (key === ' ' || event.code === 'Space') || (key === 'f' || event.code === 'KeyF');
  }

  function handleKeydown(event) {
    if (matchesHotkey(event)) {
      event.preventDefault();
      event.stopPropagation();
      open(event.target, event.currentTarget || event.target?.ownerDocument || document);
      return;
    }
    if (!isOpen) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopImmediatePropagation?.();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === 'Tab') {
      const focusable = [closeButton, ...modeButtons.values(), input].filter(element => element && !element.disabled);
      const currentIndex = focusable.indexOf(event.target);
      const nextIndex = event.shiftKey ? focusable.length - 1 : 0;
      if (currentIndex < 0 || (event.shiftKey ? currentIndex === 0 : currentIndex === focusable.length - 1)) {
        event.preventDefault();
        event.stopPropagation();
        focusable[nextIndex]?.focus({preventScroll: true});
      }
      return;
    }
    if (event.key === 'ArrowDown' && selectIndex(selectedIndex + 1)) {
      event.preventDefault();
      event.stopPropagation();
    } else if (event.key === 'ArrowUp' && selectIndex(selectedIndex < 0 ? results.length - 1 : selectedIndex - 1)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  function bindDocument(doc) {
    if (!doc || boundDocuments.has(doc)) return;
    doc.addEventListener('keydown', handleKeydown, true);
    boundDocuments.add(doc);
  }

  function bindFrame(frame) {
    if (!frame || !boundFrames.has(frame)) {
      if (!frame) return;
      boundFrames.add(frame);
      frame.addEventListener('load', () => bindFrame(frame));
    }
    try {
      const frameDoc = frame.contentDocument;
      if (!frameDoc) return;
      const oldDoc = frameDocuments.get(frame);
      if (oldDoc && oldDoc !== frameDoc) {
        oldDoc.removeEventListener('keydown', handleKeydown, true);
        boundDocuments.delete(oldDoc);
      }
      frameDocuments.set(frame, frameDoc);
      bindDocument(frameDoc);
    } catch (_) {
      // Cross-origin frames are outside this same-origin workbench shell.
    }
  }

  function bindFrames() {
    document.querySelectorAll('iframe').forEach(bindFrame);
    frameDocuments.forEach((frameDoc, frame) => {
      if (frame.isConnected === false) {
        frameDoc.removeEventListener('keydown', handleKeydown, true);
        boundDocuments.delete(frameDoc);
        frameDocuments.delete(frame);
      }
    });
  }

  function init() {
    if (!document.body) return false;
    buildUI();
    bindDocument(document);
    bindFrames();
    if (!frameObserver && typeof global.MutationObserver === 'function' && document.documentElement) {
      frameObserver = new global.MutationObserver(bindFrames);
      frameObserver.observe(document.documentElement, {childList: true, subtree: true});
    }
    if (!initialized) {
      global.addEventListener?.('gw:route-loaded', bindFrames);
      global.addEventListener?.('gw:omnibar-error', handleOmnibarError);
      initialized = true;
    }
    return true;
  }

  const api = Object.freeze({
    init,
    open,
    close,
    setMode,
    setResults,
    activateResult,
    handleKeydown,
    snapshot: () => Object.freeze({open: isOpen, mode, selectedIndex, resultCount: results.length}),
  });
  global.GWOmnibar = api;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})(window, document);
