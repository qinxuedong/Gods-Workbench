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
 * - URL parse failures emit window `gw:omnibar-error` with
 *   `{code: 'INVALID_RESULT_URL', href, message}`; `message` is diagnostic
 *   parser text and may vary by browser.
 * - The Omnibar handles this event with an inline alert and stays open so the
 *   user can retry or close it. Successful same-origin navigation emits no
 *   error event. Exceptions from `location.assign()` are not URL parse errors.
 */
(function omnibar(global, document) {
  'use strict';

  const SEARCH_HINT = /[?？]|^(请|帮我|如何|怎么|为什么|解释|分析|总结|生成|改写)/;
  const RESULT_SELECTOR = '[role="option"]';
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
    if (nextMode !== 'search' && nextMode !== 'agent') return false;
    mode = nextMode;
    if (panel) panel.dataset.mode = mode;
    modeButtons.forEach((button, name) => {
      const active = name === mode;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('is-active', active);
    });
    if (input) {
      input.placeholder = mode === 'agent' ? '向 Agent 提问，或输入 / 开始对话…' : '搜索项目、画布、资产与任务…';
      input.setAttribute('aria-label', mode === 'agent' ? '向 Agent 提问' : '搜索项目、画布、资产与任务');
    }
    if (emptyState) {
      emptyState.textContent = mode === 'agent'
        ? 'Agent 对话入口已就绪。输入问题后按 Enter 发送。'
        : '输入关键词以搜索可访问内容。';
    }
    if (manual) panel?.setAttribute('data-mode-selection', 'manual');
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
      option.addEventListener('click', () => activateResult(index));
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
    if (event?.detail?.code !== 'INVALID_RESULT_URL' || !errorState) return;
    errorState.textContent = '无法打开该结果链接，请重新搜索或关闭面板。';
    errorState.hidden = false;
  }

  function activateResult(index = selectedIndex) {
    const result = results[index];
    if (!result) return false;
    selectedIndex = index;
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-result-select', {detail: {result, index}}));
    if (typeof result.activate === 'function') result.activate(result);
    else if (typeof result.href === 'string') {
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
      if (destination.origin === global.location.origin) global.location.assign(destination.href);
    }
    close();
    return true;
  }

  function emitQuery() {
    clearError();
    const query = input?.value || '';
    if (mode === 'search' && (query.trim().startsWith('/') || SEARCH_HINT.test(query.trim()))) setMode('agent');
    global.dispatchEvent?.(new global.CustomEvent('gw:omnibar-query', {detail: {query, mode}}));
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
    [['search', '检索'], ['agent', 'Agent']].forEach(([name, label]) => {
      const button = makeButton(label, 'gw-omnibar-mode');
      button.id = `gwOmnibarMode${name === 'search' ? 'Search' : 'Agent'}`;
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
      else activateResult();
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
