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

(function floatingDismissal(global, document) {
    'use strict';

    const registry = new Map();
    let generatedId = 0;
    let registrationOrder = 0;
    let openOrder = 0;
    let observerStarted = false;
    let scanQueued = false;

    // These definitions keep older pages working while they are migrated to the
    // explicit data-floating contract.
    const LEGACY_SURFACE_DEFINITIONS = [
        {selector: '.picker-overlay', kind: 'modal', content: '[data-floating-content], .picker-modal'},
        {selector: '.gallery-lightbox', kind: 'modal', content: '[data-floating-content], #lightboxFrame, #lightboxCard'},
        {selector: '.output-lightbox', kind: 'modal', content: '[data-floating-content], .output-lightbox-shell, #outputPromptPanel'},
        {selector: '.prompt-template-modal', kind: 'modal', content: '[data-floating-content], .prompt-template-panel'},
        {selector: '.log-modal', kind: 'modal', content: '[data-floating-content], .log-panel'},
        {selector: '.shortcut-modal', kind: 'modal', content: '[data-floating-content], .shortcut-panel'},
        {selector: '.asset-manager-modal', kind: 'modal', content: '[data-floating-content], .asset-manager-panel'},
        {selector: '.asset-dialog-backdrop', kind: 'modal', content: '[data-floating-content], .asset-dialog'},
        {selector: '.image-edit-modal', kind: 'modal', content: '[data-floating-content], .image-edit-panel'},
        {selector: '.error-modal', kind: 'modal', content: '[data-floating-content], .error-panel'},
        {selector: '.image-lightbox', kind: 'modal', content: '[data-floating-content], img'},
        {selector: '.system-prompt-popover', kind: 'popover', content: 'self'},
        {selector: '#historyPopover', kind: 'popover', content: 'self'},
        {selector: '.model-picker-popover', kind: 'popover', content: 'self'},
        {selector: '.resolution-picker-popover', kind: 'popover', content: 'self'},
        {selector: '.image-preview-modal', kind: 'modal', content: '[data-floating-content]'},
        {selector: '.home-create-menu', kind: 'popover', content: 'self'},
        {selector: '.ws-trash-panel', kind: 'panel', content: 'self'},
        {selector: '.ws-create-card', kind: 'panel', content: 'self', close: 'closeCreateCard', removeOnClose: true},
        {selector: '.ws-card-pop', kind: 'popover', content: 'self'},
        {selector: '.storage-settings-overlay', kind: 'modal', content: '[data-floating-content], [role="dialog"]'},
        {selector: '.asset-video-storyboard-overlay, [data-video-storyboard-overlay]', kind: 'modal', content: '[data-floating-content], [role="dialog"]', removeOnClose: true},
        {selector: '.asset-operation-error-overlay', kind: 'modal', content: '[data-floating-content], [role="alertdialog"]'},
        {selector: '.asset-review-layer', kind: 'modal', content: '[data-floating-content], [role="dialog"], [role="alertdialog"], .asset-review-drawer, .asset-share-modal, .asset-account-modal, .asset-team-modal'},
        {selector: '.asset-detail-viewer', kind: 'modal', content: '[data-floating-content], [role="dialog"], .asset-detail-viewer-page'},
        {selector: '.smart-log-lightbox', kind: 'modal', content: '[data-floating-content], img'},
    ];
    const AUTO_DIALOG_SELECTOR = 'dialog, [role="dialog"], [role="alertdialog"]';
    const FOCUSABLE_SELECTOR = [
        'a[href]',
        'area[href]',
        'button:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[contenteditable="true"]',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    function isElement(value) {
        return Boolean(global.Element && value instanceof global.Element);
    }

    function asElement(value) {
        if (isElement(value)) return value;
        if (typeof value !== 'string' || !value.trim()) return null;
        const text = value.trim();
        const byId = document.getElementById(text);
        if (byId) return byId;
        try {
            const selected = document.querySelector(text);
            if (selected) return selected;
        } catch (_) {
            // A plain DOM id may contain characters that are not valid CSS.
        }
        for (const candidate of document.querySelectorAll('[data-floating-surface]')) {
            if (candidate.dataset.floatingSurface === text) return candidate;
        }
        return null;
    }

    function splitTokens(value) {
        return String(value || '').split(/\s+/).map(token => token.trim()).filter(Boolean);
    }

    function pathFor(event) {
        if (typeof event.composedPath === 'function') return event.composedPath();
        return event.target ? [event.target] : [];
    }

    function pathContains(path, element) {
        return Boolean(element && path.includes(element));
    }

    function pathElement(path, predicate) {
        return path.find(item => isElement(item) && predicate(item)) || null;
    }

    function generatedSurfaceName(element) {
        generatedId += 1;
        const className = typeof element.className === 'string' ? element.className : element.tagName.toLowerCase();
        return `floating-surface-${generatedId}-${className}`.replace(/[^a-zA-Z0-9_-]+/g, '-');
    }

    function isNativeDialog(element) {
        return Boolean(element && String(element.tagName || '').toLowerCase() === 'dialog');
    }

    function rememberInitialState(element) {
        if (!Object.prototype.hasOwnProperty.call(element.dataset, 'floatingInitialHidden')) {
            element.dataset.floatingInitialHidden = element.hidden ? 'true' : 'false';
        }
        if (!Object.prototype.hasOwnProperty.call(element.dataset, 'floatingInitialDisplay')) {
            element.dataset.floatingInitialDisplay = element.style.display || '';
        }
    }

    function queryElements(root, selector) {
        if (!selector || !root) return [];
        try {
            const local = Array.from(root.querySelectorAll(selector));
            if (local.length) return local;
        } catch (_) {
            return [];
        }
        // A small number of legacy overlays use a sibling as their content node
        // (for example a node popup and its separate backdrop).
        try {
            return Array.from(document.querySelectorAll(selector));
        } catch (_) {
            return [];
        }
    }

    function getContentElements(record) {
        if (record.contentElement?.isConnected) return [record.contentElement];
        const selector = record.content ?? record.element.dataset.floatingContent;
        if (selector === 'self' || selector === 'true') return [record.element];
        if (selector) return queryElements(record.element, selector).filter(element => element !== record.element);

        const marked = queryElements(record.element, '[data-floating-content]').filter(element => element !== record.element);
        if (marked.length) return marked;
        if (isNativeDialog(record.element)) return Array.from(record.element.children);
        if (record.kind === 'popover' || record.kind === 'panel') return [record.element];
        return [];
    }

    function rawIsOpen(record) {
        const element = record.element;
        if (!element?.isConnected) return false;
        if (typeof record.isOpen === 'function') return Boolean(record.isOpen(element));
        if (isNativeDialog(element)) return Boolean(element.open);
        if (element.hidden || element.getAttribute('aria-hidden') === 'true') return false;

        const openClass = element.dataset.floatingOpenClass;
        if (openClass && splitTokens(openClass).some(token => element.classList.contains(token))) return true;
        if (element.classList.contains('open') || element.classList.contains('visible')) return true;
        if (element.classList.contains('active')) return true;

        const style = global.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function syncOpenState(record) {
        const open = rawIsOpen(record);
        if (open && !record.wasOpen) {
            record.openedAt = ++openOrder;
            record.justOpened = true;
            const currentFocus = isElement(document.activeElement) ? document.activeElement : null;
            if (currentFocus && !record.element.contains(currentFocus) && !currentFocus.matches?.('[data-floating-surface]')) {
                record.previousFocus = currentFocus;
            }
        } else {
            record.justOpened = false;
        }
        record.wasOpen = open;
        return open;
    }

    function isOpen(record) {
        return rawIsOpen(record);
    }

    function triggerFor(record) {
        const value = record.trigger ?? record.element.dataset.floatingTrigger;
        return asElement(value);
    }

    function parentElementFor(record) {
        const value = record.parent ?? record.element.dataset.floatingParent;
        if (value && String(value).trim() && String(value).trim().toLowerCase() !== 'none') {
            const explicit = asElement(value);
            if (explicit && explicit !== record.element) return explicit;
        }

        let ancestor = record.element.parentElement;
        while (ancestor) {
            if (registry.has(ancestor)) return ancestor;
            ancestor = ancestor.parentElement;
        }
        return null;
    }

    function parentRecordFor(record) {
        const parentElement = parentElementFor(record);
        return parentElement ? registry.get(parentElement) || null : null;
    }

    function recordChain(record, includeSelf = true) {
        const chain = [];
        const seen = new Set();
        let current = includeSelf ? record : parentRecordFor(record);
        while (current && !seen.has(current)) {
            chain.push(current);
            seen.add(current);
            current = parentRecordFor(current);
        }
        return chain;
    }

    function isTriggerPath(record, path) {
        const trigger = triggerFor(record);
        if (pathContains(path, trigger)) return true;
        const id = record.element.id;
        if (!id) return false;
        return path.some(item => isElement(item)
            && (item.getAttribute('aria-controls') === id || item.getAttribute('data-floating-trigger') === id));
    }

    function triggerElementFromPath(record, path) {
        const trigger = triggerFor(record);
        if (pathContains(path, trigger)) return trigger;
        const id = record.element.id;
        if (!id) return null;
        return pathElement(path, element => element.getAttribute('aria-controls') === id || element.getAttribute('data-floating-trigger') === id);
    }

    function isInsideSurface(record, path) {
        const element = record.element;
        const contents = getContentElements(record);
        if (!pathContains(path, element)) return contents.some(content => pathContains(path, content));
        if (isNativeDialog(element)) {
            // For a native dialog the dialog node itself is the backdrop target;
            // its descendants are the actual floating content.
            return path[0] !== element && contents.some(content => pathContains(path, content));
        }
        if (contents.length) return contents.some(content => pathContains(path, content));
        return record.content === 'self' || record.element.dataset.floatingContent === 'self';
    }

    function resolveCloseFunction(record) {
        if (typeof record.close === 'function') return record.close;
        const name = record.close ?? record.element.dataset.floatingClose;
        if (!name) return null;
        const fn = String(name).split('.').reduce((value, key) => value?.[key], global);
        return typeof fn === 'function' ? fn : null;
    }

    function closeByState(record) {
        const element = record.element;
        if (isNativeDialog(element)) {
            if (element.open) element.close();
            return;
        }
        const openClasses = splitTokens(element.dataset.floatingOpenClass || 'open visible active');
        openClasses.forEach(token => element.classList.remove(token));
        splitTokens(element.dataset.floatingClosedClass).forEach(token => element.classList.add(token));
        if (element.dataset.floatingCloseStyle !== undefined) element.style.display = element.dataset.floatingCloseStyle;
        else if (element.dataset.floatingInitialDisplay) element.style.display = element.dataset.floatingInitialDisplay;
        if (element.dataset.floatingInitialHidden === 'true') element.hidden = true;
        if (element.hasAttribute('aria-hidden')) element.setAttribute('aria-hidden', 'true');
        if (record.removeOnClose || element.dataset.floatingRemoveOnClose === 'true') element.remove();
    }

    function restoreFocus(record) {
        // A registered opener is the explicit focus contract for this surface.
        // The element that happened to be focused inside the opener (for example
        // a nested action button) is only a fallback when no opener was supplied.
        const target = record.lastTrigger || triggerFor(record) || record.previousFocus || asElement(record.element.dataset.floatingTrigger);
        if (!target?.isConnected || typeof target.focus !== 'function' || target.hidden) return;
        global.requestAnimationFrame(() => target.focus({preventScroll: true}));
    }

    function closeRecord(record) {
        if (record.closing || !isOpen(record)) return false;
        record.closing = true;
        const fn = resolveCloseFunction(record);
        try {
            if (fn) {
                const args = record.element.dataset.floatingCloseArgs;
                if (args) {
                    fn(...args.split(',').map(value => {
                        const normalized = value.trim();
                        if (normalized === 'false') return false;
                        if (normalized === 'true') return true;
                        return normalized;
                    }));
                } else if (/^toggle/i.test(String(record.close ?? record.element.dataset.floatingClose ?? ''))) {
                    fn(false);
                } else {
                    fn();
                }
            } else {
                const closeButton = record.element.querySelector('[data-floating-close]:not([disabled])');
                if (closeButton) closeButton.click();
                else closeByState(record);
            }
        } catch (error) {
            global.console?.warn?.('[FloatingDismissal] close failed', error);
            closeByState(record);
        }
        record.closing = false;
        const closed = !isOpen(record);
        if (closed) restoreFocus(record);
        return closed;
    }

    // Public callers may close only a registered DOM root, never an arbitrary
    // function name. This preserves the shared focus-restoration path.
    function closeSurface(element) {
        if (!isElement(element)) return false;
        scan();
        const record = registry.get(element);
        return record ? closeRecord(record) : false;
    }

    function defaultKind(element) {
        if (isNativeDialog(element)) return 'native-dialog';
        if (element.matches?.('.smart-popover, [role="menu"], [role="listbox"]')) return 'popover';
        return 'overlay';
    }

    function definitionFor(element) {
        return LEGACY_SURFACE_DEFINITIONS.find(definition => {
            try { return element.matches(definition.selector); } catch (_) { return false; }
        });
    }

    function register(elementOrSelector, options = {}, {fromScan = false} = {}) {
        const element = asElement(elementOrSelector);
        if (!element) return () => {};
        rememberInitialState(element);
        if (typeof options.surface === 'string' && options.surface.trim()) {
            element.dataset.floatingSurface = options.surface.trim();
        } else if (!element.dataset.floatingSurface) {
            element.dataset.floatingSurface = generatedSurfaceName(element);
        }

        let record = registry.get(element);
        if (!record) {
            record = {
                element,
                sequence: ++registrationOrder,
                wasOpen: false,
                openedAt: 0,
                closing: false,
                explicitOptions: new Set(),
            };
            registry.set(element, record);
        }
        Object.entries(options).forEach(([key, value]) => {
            if (value === undefined || key === 'fromScan') return;
            if (fromScan && record.explicitOptions.has(key)) return;
            record[key] = value;
            if (!fromScan) record.explicitOptions.add(key);
        });
        if (options.content && typeof options.content !== 'string') record.contentElement = asElement(options.content);
        if (!element.dataset.floatingKind && typeof options.kind === 'string') element.dataset.floatingKind = options.kind;
        if (!element.dataset.floatingContent && typeof options.content === 'string') element.dataset.floatingContent = options.content;
        if (!element.dataset.floatingTrigger && typeof options.trigger === 'string') element.dataset.floatingTrigger = options.trigger;
        if (!element.dataset.floatingParent && typeof options.parent === 'string') element.dataset.floatingParent = options.parent;
        if (!element.dataset.floatingDismiss) element.dataset.floatingDismiss = 'outside';
        if (!element.dataset.floatingEscape) element.dataset.floatingEscape = 'close-only';
        syncOpenState(record);
        return () => registry.delete(element);
    }

    function mark(elementOrSelector, options = {}) {
        const element = asElement(elementOrSelector);
        if (!element) return () => {};
        if (typeof options.surface === 'string' && options.surface.trim()) {
            element.dataset.floatingSurface = options.surface.trim();
        } else if (!element.dataset.floatingSurface) {
            element.dataset.floatingSurface = generatedSurfaceName(element);
        }
        Object.entries(options).forEach(([key, value]) => {
            if (value === undefined || value === null || typeof value !== 'string') return;
            const dataKey = `floating${key[0].toUpperCase()}${key.slice(1)}`;
            element.dataset[dataKey] = value;
        });
        if (!element.dataset.floatingDismiss) element.dataset.floatingDismiss = 'outside';
        if (!element.dataset.floatingEscape) element.dataset.floatingEscape = 'close-only';
        return register(element, options);
    }

    function collectCandidates(root) {
        const candidates = [];
        const addMatches = selector => {
            try {
                if (isElement(root) && root.matches(selector)) candidates.push(root);
                if (root.querySelectorAll) candidates.push(...root.querySelectorAll(selector));
            } catch (_) {}
        };
        addMatches('[data-floating-surface]');
        LEGACY_SURFACE_DEFINITIONS.forEach(definition => addMatches(definition.selector));

        const explicitRoots = new Set(candidates);
        try {
            if (isElement(root) && root.matches(AUTO_DIALOG_SELECTOR) && !explicitRoots.has(root)) candidates.push(root);
            if (root.querySelectorAll) {
                root.querySelectorAll(AUTO_DIALOG_SELECTOR).forEach(element => {
                    let parent = element.parentElement;
                    let nested = false;
                    while (parent) {
                        if (explicitRoots.has(parent)) { nested = true; break; }
                        parent = parent.parentElement;
                    }
                    if (!nested) candidates.push(element);
                });
            }
        } catch (_) {}
        return [...new Set(candidates)];
    }

    function pruneDisconnected() {
        registry.forEach((record, element) => {
            if (!element.isConnected) registry.delete(element);
        });
    }

    function scan(root = document) {
        pruneDisconnected();
        collectCandidates(root).forEach(element => {
            const definition = definitionFor(element) || {};
            const dataset = element.dataset;
            register(element, {
                kind: dataset.floatingKind || definition.kind || defaultKind(element),
                content: dataset.floatingContent || definition.content,
                close: dataset.floatingClose || definition.close,
                trigger: dataset.floatingTrigger || definition.trigger,
                parent: dataset.floatingParent || definition.parent,
                removeOnClose: definition.removeOnClose,
                isOpen: undefined,
            }, {fromScan: true});
            if (!dataset.floatingDismiss) dataset.floatingDismiss = 'outside';
            if (!dataset.floatingEscape) dataset.floatingEscape = 'close-only';
        });
    }

    function scheduleScan() {
        if (scanQueued) return;
        scanQueued = true;
        global.queueMicrotask(() => {
            scanQueued = false;
            scan();
        });
    }

    function rememberTriggers(event) {
        const path = pathFor(event);
        const active = isElement(document.activeElement) ? document.activeElement : null;
        registry.forEach(record => {
            const trigger = triggerElementFromPath(record, path);
            if (!trigger) return;
            record.lastTrigger = trigger;
            if (active && active !== record.element && !record.element.contains(active)) record.previousFocus = active;
        });
    }

    function priorityOf(record) {
        const value = Number(record.element.dataset.floatingPriority || 0);
        return Number.isFinite(value) ? value : 0;
    }

    function topmostFirst(left, right) {
        return priorityOf(right) - priorityOf(left)
            || Number(right.openedAt || 0) - Number(left.openedAt || 0)
            || Number(right.sequence || 0) - Number(left.sequence || 0);
    }

    function openRecords() {
        return [...registry.values()].filter(isOpen).sort(topmostFirst);
    }

    function pathTouchesSurface(record, path) {
        return pathContains(path, record.element) || isInsideSurface(record, path);
    }

    function triggerPathRecords(path) {
        return [...registry.values()].filter(record => isTriggerPath(record, path));
    }

    function hitSurfaceForPath(open, path) {
        return open.find(record => pathTouchesSurface(record, path)) || null;
    }

    function contentSurfaceHits(open, path) {
        return open.filter(record => isInsideSurface(record, path));
    }

    function closeOrder(records) {
        return [...records].sort((left, right) => {
            const depth = recordChain(right).length - recordChain(left).length;
            return depth || topmostFirst(left, right);
        });
    }

    function isOutsideDismissEnabled(record) {
        // "manual" was used by an earlier draft. Treat it as outside so old
        // markup cannot silently bypass the current global rule.
        return String(record.element.dataset.floatingDismiss || 'outside').toLowerCase() !== 'none';
    }

    function isModalRecord(record) {
        const kind = String(record.kind || record.element.dataset.floatingKind || '').toLowerCase();
        return isNativeDialog(record.element) || kind === 'modal' || kind === 'native-dialog' || record.element.getAttribute('aria-modal') === 'true';
    }

    function focusableElements(record) {
        const roots = getContentElements(record);
        if (!roots.length && record.element.matches?.(FOCUSABLE_SELECTOR)) return [record.element];
        const elements = [];
        roots.forEach(root => {
            if (root.matches?.(FOCUSABLE_SELECTOR)) elements.push(root);
            if (root.querySelectorAll) elements.push(...root.querySelectorAll(FOCUSABLE_SELECTOR));
        });
        return elements.filter(element => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
    }

    function focusSurface(record) {
        if (!record || !isOpen(record) || !isModalRecord(record)) return;
        const target = focusableElements(record)[0]
            || record.element.querySelector('[aria-labelledby], h1, h2, h3')
            || record.element;
        if (target && typeof target.focus === 'function') {
            if (!target.hasAttribute('tabindex') && target === record.element) target.setAttribute('tabindex', '-1');
            global.requestAnimationFrame(() => target.focus({preventScroll: true}));
        }
    }

    function focusOpenedSurface() {
        const newlyOpened = [...registry.values()].filter(record => record.justOpened && isOpen(record)).sort(topmostFirst);
        focusSurface(newlyOpened[0]);
    }

    function trapModalTab(event) {
        const top = openRecords().find(isModalRecord);
        if (!top || top.element.dataset.floatingFocusTrap === 'false') return false;
        const focusables = focusableElements(top);
        if (!focusables.length) {
            event.preventDefault();
            event.stopImmediatePropagation();
            top.element.focus?.({preventScroll: true});
            return true;
        }
        const current = isElement(document.activeElement) ? document.activeElement : null;
        const index = focusables.indexOf(current);
        if (event.shiftKey && (index <= 0 || !top.element.contains(current))) {
            event.preventDefault();
            event.stopImmediatePropagation();
            focusables[focusables.length - 1].focus({preventScroll: true});
            return true;
        }
        if (!event.shiftKey && (index < 0 || index === focusables.length - 1)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            focusables[0].focus({preventScroll: true});
            return true;
        }
        return false;
    }

    function handlePointerDown(event) {
        scheduleScan();
    }

    function scheduleOpenedSurfaceFocus(triggerRecords = []) {
        // Capture listeners run before an inline opener. Defer until the next
        // frame so mouse, Enter, and Space all see the opened surface.
        global.requestAnimationFrame(() => {
            scan();
            // Mutation observers can scan first and consume `justOpened`.
            // The trigger record remains stable across mouse and keyboard clicks.
            const openedTrigger = triggerRecords.filter(isOpen).sort(topmostFirst)[0];
            if (openedTrigger) {
                focusSurface(openedTrigger);
                return;
            }
            focusOpenedSurface();
        });
    }

    function handleClick(event) {
        scan();
        const path = pathFor(event);
        if (!path.length) return;

        // Click is the common activation path for mouse, Enter, and Space.
        // Record the trigger here so a later close can always restore focus.
        rememberTriggers(event);
        const triggers = triggerPathRecords(path);
        const open = openRecords();
        if (!open.length) {
            if (triggers.length) scheduleOpenedSurfaceFocus(triggers);
            return;
        }

        const surfaceHit = hitSurfaceForPath(open, path);
        const contentHits = contentSurfaceHits(open, path);
        const keep = new Set();
        contentHits.forEach(record => recordChain(record).forEach(item => keep.add(item)));
        triggers.forEach(record => {
            if (isOpen(record)) recordChain(record).forEach(item => keep.add(item));
        });

        // A backdrop/root hit is outside that surface's content. Keep only its
        // parent chain so a child menu closes without collapsing its owner.
        if (surfaceHit && !isInsideSurface(surfaceHit, path)) {
            recordChain(surfaceHit, false).forEach(item => keep.add(item));
        }

        const dismissible = open.filter(record => isOutsideDismissEnabled(record) && !keep.has(record));
        closeOrder(dismissible).forEach(record => closeRecord(record));

        // A click on another registered opener may close the old surface and
        // continue to the opener so the new surface can open in one gesture.
        const clickIsInsideFloatingContent = contentHits.length > 0;
        const clickIsRegisteredTrigger = triggers.length > 0;
        if (dismissible.length && !clickIsInsideFloatingContent && !clickIsRegisteredTrigger) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
        scheduleOpenedSurfaceFocus(triggers);
    }

    function handleFocusIn(event) {
        scan();
        const path = pathFor(event);
        openRecords().filter(record => String(record.kind || '').toLowerCase() === 'popover' || String(record.kind || '').toLowerCase() === 'panel').forEach(record => {
            if (isTriggerPath(record, path) || isInsideSurface(record, path)) return;
            closeRecord(record);
        });
    }

    function handleKeydown(event) {
        scan();
        if (event.key === 'Tab' && trapModalTab(event)) return;
        if (event.key !== 'Escape') return;
        const top = openRecords().find(record => String(record.element.dataset.floatingEscape || 'close-only').toLowerCase() !== 'none');
        if (!top) return;
        const closed = closeRecord(top);
        if (closed || isOpen(top)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }

    function startObserver() {
        if (observerStarted || !document.documentElement || typeof global.MutationObserver !== 'function') return;
        observerStarted = true;
        new global.MutationObserver(changes => {
            changes.forEach(change => {
                change.addedNodes.forEach(node => {
                    if (isElement(node)) scan(node);
                });
                change.removedNodes.forEach(node => {
                    if (!isElement(node)) return;
                    registry.forEach((record, element) => {
                        if (element === node || node.contains(element)) registry.delete(element);
                    });
                });
                if (change.type === 'attributes') scheduleScan();
            });
        }).observe(document.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: [
                'data-floating-surface',
                'data-floating-content',
                'data-floating-close',
                'data-floating-trigger',
                'data-floating-parent',
                'data-floating-kind',
                'data-floating-dismiss',
                'data-floating-escape',
            ],
        });
    }

    document.addEventListener('pointerdown', handlePointerDown, true);
    document.addEventListener('click', handleClick, true);
    document.addEventListener('focusin', handleFocusIn, true);
    document.addEventListener('keydown', handleKeydown, true);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            scan();
            startObserver();
        }, {once: true});
    } else {
        scan();
        startObserver();
    }

    global.FloatingDismissal = Object.freeze({
        register,
        mark,
        scan,
        closeSurface,
        closeAll() { openRecords().forEach(closeRecord); },
        hasOpen() { return openRecords().length > 0; },
    });
})(window, document);
