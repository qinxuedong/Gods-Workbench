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

/* Shell-owned actions; loading this module installs no listeners. */
(() => {
  'use strict';
  let actions = null;
  let feedback = null;
  let feedbackTimer = null;
  const popupSelector = 'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"], [role="menu"], [popover]';

  function guarded(element) {
    return element?.isContentEditable || element?.closest?.(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="spinbutton"]'
    );
  }

  function popupOpen() {
    return [...document.querySelectorAll(popupSelector)].some(element => {
      if (element.hidden || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
      const style = window.getComputedStyle(element);
      return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
    });
  }

  function announce(message) {
    clearTimeout(feedbackTimer);
    if (!feedback) {
      feedback = document.createElement('div');
      feedback.className = 'context-hotkey-feedback show';
      feedback.setAttribute('role', 'status');
      feedback.setAttribute('aria-live', 'polite');
      feedback.style.pointerEvents = 'none';
      document.body.appendChild(feedback);
    }
    feedback.textContent = message;
    feedbackTimer = setTimeout(() => { feedback?.remove(); feedback = null; }, 1500);
  }

  function keydown(event) {
    if (!actions || event.defaultPrevented || event.isComposing || event.keyCode === 229
      || event.repeat || event.altKey || event.shiftKey || !(event.ctrlKey || event.metaKey)
      || (event.target?.ownerDocument && event.target.ownerDocument !== document)
      || guarded(document.activeElement) || (event.composedPath?.() || [event.target]).some(guarded)
      || popupOpen()) return;
    let callback;
    let argument;
    let message;
    switch (event.key) {
      case 'k': case 'K': callback = actions.focusToolbar; message = '已聚焦上下文工具栏'; break;
      case '\\': callback = actions.toggleTree; message = '已切换上下文树'; break;
      case 'ArrowUp': callback = actions.navigateSibling; argument = -1; message = '已转到上一个同级节点'; break;
      case 'ArrowDown': callback = actions.navigateSibling; argument = 1; message = '已转到下一个同级节点'; break;
      default: return;
    }
    try {
      if (typeof callback === 'function' && callback(argument) === true) {
        event.preventDefault();
        announce(message);
      }
    } catch (_) { /* A failed optional action leaves the original key alone. */ }
  }

  function destroy() {
    document.removeEventListener('keydown', keydown);
    actions = null;
    clearTimeout(feedbackTimer);
    feedback?.remove();
    feedback = null;
  }

  window.ContextHotkeys = {
    configure(options = {}) {
      destroy();
      if (!options) return;
      actions = {focusToolbar: options.focusToolbar, toggleTree: options.toggleTree, navigateSibling: options.navigateSibling};
      document.addEventListener('keydown', keydown);
    },
    destroy,
  };
})();
