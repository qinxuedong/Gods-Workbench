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

(function shortcuts(global, document) {
  'use strict';

  const ITEM_SELECTOR = [
    '[data-gw-shortcut-item]',
    '[data-registry-card]',
    '[data-local-card]',
    '[data-localup-card]',
    '[data-asset-card]',
    '[data-prompt-row]',
    '[data-workflow-card]',
    '[data-canvas-asset-card]',
    '[data-asset-index]'
  ].join(',');
  const SCOPE_SELECTOR = [
    '[data-gw-shortcut-scope]',
    '[data-gw-shortcut-list]',
    '[role="grid"]',
    '[role="listbox"]'
  ].join(',');
  const FOCUSABLE_SELECTOR = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[contenteditable="true"]',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const PREVIEW_SELECTOR = [
    '[data-gw-shortcut-preview]',
    '[data-registry-preview]',
    '[data-asset-preview]',
    '[data-canvas-asset-preview]',
    '[data-local-preview]',
    '[data-localup-preview]',
    '[data-preview]',
    'button[aria-label*="预览"]',
    'button[title*="预览"]',
    'button[title*="放大"]'
  ].join(',');
  const SURFACE_SELECTOR = [
    '[data-gw-shortcut-surface]',
    '[data-floating-surface]',
    'dialog[open]',
    '[role="dialog"]'
  ].join(',');
  const FOCUS_SCOPE_SELECTOR = [
    '[data-gw-focus-scope]',
    '[data-gw-shortcut-surface]',
    '[data-floating-surface]',
    'dialog[open]',
    '[role="dialog"]'
  ].join(',');

  let bound = false;
  let lastFocused = null;

  function asArray(value) {
    return Array.from(value || []);
  }

  function isElement(value) {
    return Boolean(value && typeof value.matches === 'function' && typeof value.contains === 'function');
  }

  function classHas(element, name) {
    return Boolean(element?.classList?.contains?.(name)
      || element?.classList?.has?.(name));
  }

  function isEditable(target) {
    const tag = String(target?.tagName || '').toUpperCase();
    return ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)
      || Boolean(target?.isContentEditable)
      || Boolean(target?.closest?.('[contenteditable="true"], [role="textbox"]'));
  }

  function isVisible(element) {
    if (!isElement(element) || element.hidden || element.disabled) return false;
    if (element.getAttribute?.('aria-hidden') === 'true') return false;
    const style = global.getComputedStyle?.(element);
    return !style || (style.display !== 'none' && style.visibility !== 'hidden');
  }

  function queryAll(selector, root = document) {
    try { return asArray(root?.querySelectorAll?.(selector)); } catch (_) { return []; }
  }

  function unique(elements) {
    return [...new Set(elements.filter(isElement))];
  }

  function itemKind(element) {
    if (element?.dataset?.gwShortcutItem !== undefined) return 'gw-shortcut-item';
    const attributes = [
      'registryCard', 'localCard', 'localupCard', 'assetCard', 'promptRow',
      'workflowCard', 'canvasAssetCard', 'assetIndex'
    ];
    return attributes.find(attribute => element?.dataset?.[attribute] !== undefined) || '';
  }

  function shortcutItems(anchor = null) {
    const candidates = unique(queryAll(ITEM_SELECTOR)).filter(isVisible);
    const scope = anchor?.closest?.(SCOPE_SELECTOR)
      || document.querySelector?.('[data-gw-shortcut-scope], [data-gw-shortcut-list]');
    const scoped = scope ? candidates.filter(item => scope.contains(item)) : candidates;
    const kind = itemKind(anchor);
    return kind ? scoped.filter(item => itemKind(item) === kind) : scoped;
  }

  function itemFromTarget(target) {
    if (!target) return null;
    if (isElement(target) && target.matches(ITEM_SELECTOR)) return target;
    return target.closest?.(ITEM_SELECTOR) || null;
  }

  function focusItem(item) {
    if (!item) return false;
    item.setAttribute?.('aria-current', 'true');
    const siblings = shortcutItems(item);
    siblings.forEach(candidate => {
      if (candidate !== item && candidate.getAttribute?.('aria-current') === 'true') {
        candidate.setAttribute('aria-current', 'false');
      }
    });
    try { item.focus({preventScroll: true}); } catch (_) { item.focus?.(); }
    item.scrollIntoView?.({block: 'nearest'});
    return true;
  }

  function currentItem(items, target) {
    const focused = itemFromTarget(target) || itemFromTarget(document.activeElement);
    if (focused && items.includes(focused)) return focused;
    return items.find(item => item.getAttribute?.('aria-current') === 'true'
      || classHas(item, 'active') || classHas(item, 'selected')) || null;
  }

  function move(delta, target = document.activeElement) {
    const items = shortcutItems(itemFromTarget(target));
    if (!items.length) return false;
    const current = currentItem(items, target);
    const index = current ? items.indexOf(current) : (delta > 0 ? -1 : 0);
    const next = items[(index + delta + items.length) % items.length];
    return focusItem(next);
  }

  function previewItem(item) {
    if (!item) return false;
    const trigger = item.matches?.(PREVIEW_SELECTOR) ? item : item.querySelector?.(PREVIEW_SELECTOR);
    if (trigger?.click) {
      trigger.click();
      return true;
    }
    if (typeof global.CustomEvent === 'function' && global.dispatchEvent) {
      global.dispatchEvent(new global.CustomEvent('gw:shortcut-preview', {detail: {item}}));
      return true;
    }
    return false;
  }

  function rememberFocus(event) {
    if (isElement(event?.target)) lastFocused = event.target;
  }

  function isOpenSurface(surface) {
    if (!isVisible(surface)) return false;
    if (surface.matches?.('dialog[open]')) return true;
    if (surface.dataset?.open === 'false' || surface.dataset?.shortcutOpen === 'false') return false;
    if (surface.dataset?.open === 'true' || surface.dataset?.shortcutOpen === 'true') return true;
    if (classHas(surface, 'open') || classHas(surface, 'visible') || classHas(surface, 'active')) return true;
    return surface.getAttribute?.('aria-hidden') !== 'true';
  }

  function previousSibling(element) {
    const siblings = element?.parentElement?.children;
    if (!siblings) return null;
    const index = Array.from(siblings).indexOf(element);
    return index > 0 ? siblings[index - 1] : null;
  }

  function surfaceTrigger(surface) {
    const selector = surface?.dataset?.gwShortcutTrigger || surface?.dataset?.floatingTrigger;
    if (selector) {
      try {
        const bySelector = document.querySelector(selector) || document.getElementById?.(selector);
        if (bySelector) return bySelector;
      } catch (_) {}
    }
    const id = surface?.id;
    if (id) {
      try {
        const control = document.querySelector(`[aria-controls="${CSS.escape(id)}"]`);
        if (control) return control;
      } catch (_) {}
    }
    if (lastFocused && !surface.contains(lastFocused)) return lastFocused;
    return previousSibling(surface);
  }

  function focusSurfaceTrigger(surface) {
    const trigger = surfaceTrigger(surface);
    if (!trigger || !isVisible(trigger) || typeof trigger.focus !== 'function') return;
    if (document.activeElement !== trigger) {
      try { trigger.focus({preventScroll: true}); } catch (_) { trigger.focus(); }
    }
  }

  function closeSurface(surface) {
    if (!surface) return false;
    const close = surface.querySelector?.('[data-gw-shortcut-close], [data-floating-close]');
    if (close?.click) close.click();
    else if (typeof surface.close === 'function') surface.close();
    else {
      surface.hidden = true;
      surface.setAttribute?.('aria-hidden', 'true');
      surface.classList?.remove?.('open');
    }
    focusSurfaceTrigger(surface);
    return true;
  }

  function openSurfaces() {
    return unique(queryAll(SURFACE_SELECTOR)).filter(isOpenSurface);
  }

  function closeTopSurface(event) {
    const dismissal = global.FloatingDismissal;
    if (dismissal?.hasOpen?.()) {
      dismissal.closeAll?.();
      event.preventDefault?.();
      event.stopImmediatePropagation?.();
      return true;
    }
    const surface = openSurfaces().at(-1);
    if (!surface) return false;
    closeSurface(surface);
    event.preventDefault?.();
    event.stopImmediatePropagation?.();
    return true;
  }

  function focusables(scope) {
    return queryAll(FOCUSABLE_SELECTOR, scope).filter(isVisible);
  }

  function trapTab(event) {
    const scope = event.target?.closest?.(FOCUS_SCOPE_SELECTOR)
      || openSurfaces().at(-1);
    if (!scope || !isOpenSurface(scope)) return false;
    const elements = focusables(scope);
    if (!elements.length) return false;
    const current = elements.indexOf(document.activeElement);
    const next = event.shiftKey
      ? elements[current <= 0 ? elements.length - 1 : current - 1]
      : elements[current === elements.length - 1 || current < 0 ? 0 : current + 1];
    if (!next) return false;
    event.preventDefault?.();
    event.stopImmediatePropagation?.();
    try { next.focus({preventScroll: true}); } catch (_) { next.focus(); }
    return true;
  }

  function interactiveTarget(target, item) {
    if (!target || !item) return false;
    if (isEditable(target)) return true;
    const control = target.closest?.('button, a, input, textarea, select, [role="button"]');
    return Boolean(control && control !== item);
  }

  function handleKeydown(event) {
    if (!event) return;
    if (event.key === 'Tab' && trapTab(event)) return;
    if (event.key === 'Escape' && closeTopSurface(event)) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    const item = itemFromTarget(event.target) || itemFromTarget(document.activeElement);
    if (event.key === ' ' || event.code === 'Space') {
      if (!item || interactiveTarget(event.target, item)) return;
      if (previewItem(item)) {
        event.preventDefault?.();
        event.stopImmediatePropagation?.();
      }
      return;
    }
    if (!item || interactiveTarget(event.target, item) || isEditable(event.target)) return;
    const key = String(event.key || '').toLowerCase();
    const delta = key === 'j' || event.key === 'ArrowDown' || event.key === 'ArrowRight'
      ? 1
      : key === 'k' || event.key === 'ArrowUp' || event.key === 'ArrowLeft'
        ? -1
        : 0;
    if (!delta || !move(delta, item)) return;
    event.preventDefault?.();
    event.stopImmediatePropagation?.();
  }

  function bind() {
    if (bound) return api;
    bound = true;
    document.addEventListener('keydown', handleKeydown, true);
    document.addEventListener('focusin', rememberFocus, true);
    global.addEventListener?.('gw:route-loaded', rebind);
    return api;
  }

  function unbind() {
    if (!bound) return api;
    bound = false;
    document.removeEventListener?.('keydown', handleKeydown, true);
    document.removeEventListener?.('focusin', rememberFocus, true);
    global.removeEventListener?.('gw:route-loaded', rebind);
    return api;
  }

  function rebind() {
    return bind();
  }

  const api = Object.freeze({
    bind,
    unbind,
    rebind,
    handleKeydown,
    move,
    previewItem,
    closeTopSurface,
    trapTab,
    shortcutItems
  });
  global.WorkbenchShortcuts = api;
  global.GWShortcuts = api;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, {once: true});
  else bind();
})(window, document);
