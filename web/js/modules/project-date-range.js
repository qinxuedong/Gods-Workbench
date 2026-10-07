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

(function (window, document) {
  'use strict';

  const instances = new WeakMap();
  const pad = value => String(value).padStart(2, '0');
  const dayKey = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const dateFromKey = key => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key || ''));
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const dayStart = date => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const timestampDate = value => {
    const timestamp = Number(value || 0);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return null;
    const date = new Date(timestamp < 1e12 ? timestamp * 1000 : timestamp);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const formatDate = date => date ? `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` : '';

  function labelFor(instance) {
    const start = timestampDate(instance.start.value);
    const end = timestampDate(instance.end.value);
    if (!start && !end) return '选择项目排期';
    if (start && end) return `${formatDate(start)} 至 ${formatDate(end)}`;
    return `开始：${formatDate(start)} · 请选择结束日期`;
  }

  function sync(instance) {
    instance.label.textContent = labelFor(instance);
    instance.trigger.setAttribute('aria-label', labelFor(instance));
  }

  function render(instance) {
    const month = instance.month;
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const gridStart = new Date(first);
    gridStart.setDate(1 - first.getDay());
    const start = timestampDate(instance.start.value);
    const end = timestampDate(instance.end.value);
    const startKey = start ? dayKey(start) : '';
    const endKey = end ? dayKey(end) : '';
    const monthLabel = new Intl.DateTimeFormat('zh-CN', {year: 'numeric', month: 'long'}).format(first);
    const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
    const todayKey = dayKey(new Date());
    const cells = [];
    for (let index = 0; index < 42; index += 1) {
      const date = new Date(gridStart);
      date.setDate(gridStart.getDate() + index);
      const key = dayKey(date);
      const outside = date.getMonth() !== month.getMonth();
      const between = startKey && endKey && key > startKey && key < endKey;
      const classes = ['project-date-range-day'];
      if (outside) classes.push('is-other');
      if (key === todayKey) classes.push('is-today');
      if (key === startKey) classes.push('is-start');
      if (key === endKey) classes.push('is-end');
      if (between) classes.push('is-between');
      cells.push(`<button type="button" class="${classes.join(' ')}" data-date-range-day="${key}" aria-label="${key}">${date.getDate()}</button>`);
    }
    instance.popover.innerHTML = `<div class="project-date-range-head"><button type="button" class="project-date-range-nav" data-date-range-prev aria-label="上个月">‹</button><strong>${monthLabel}</strong><button type="button" class="project-date-range-nav" data-date-range-next aria-label="下个月">›</button></div><div class="project-date-range-weekdays">${weekdays.map(item => `<span>${item}</span>`).join('')}</div><div class="project-date-range-days">${cells.join('')}</div><p class="project-date-range-hint">${start && !end ? '请选择第二个日期作为结束时间' : '点击第一个日期设置开始时间'}</p>`;
  }

  function close(instance) {
    instance.popover.hidden = true;
    instance.trigger.setAttribute('aria-expanded', 'false');
  }

  function open(instance) {
    document.querySelectorAll('[data-project-date-range] .project-date-range-popover:not([hidden])').forEach(popover => {
      const other = popover.closest('[data-project-date-range]');
      if (other && other !== instance.root) close(instances.get(other));
    });
    render(instance);
    instance.popover.hidden = false;
    instance.trigger.setAttribute('aria-expanded', 'true');
  }

  function bind(root) {
    (root || document).querySelectorAll('[data-project-date-range]').forEach(container => {
      if (instances.has(container)) return;
      const trigger = container.querySelector('[data-date-range-trigger]');
      const label = container.querySelector('[data-date-range-label]');
      const start = container.querySelector('[data-date-range-start]');
      const end = container.querySelector('[data-date-range-end]');
      const popover = container.querySelector('[data-date-range-popover]');
      if (!trigger || !label || !start || !end || !popover) return;
      const selectedStart = timestampDate(start.value);
      const instance = {root: container, trigger, label, start, end, popover, month: new Date((selectedStart || new Date()).getFullYear(), (selectedStart || new Date()).getMonth(), 1)};
      instances.set(container, instance);
      sync(instance);
      trigger.addEventListener('click', () => popover.hidden ? open(instance) : close(instance));
      popover.addEventListener('click', event => {
        const nav = event.target.closest('[data-date-range-prev],[data-date-range-next]');
        if (nav) {
          instance.month = new Date(instance.month.getFullYear(), instance.month.getMonth() + (nav.hasAttribute('data-date-range-next') ? 1 : -1), 1);
          render(instance);
          return;
        }
        const dayButton = event.target.closest('[data-date-range-day]');
        if (!dayButton) return;
        const date = dateFromKey(dayButton.dataset.dateRangeDay);
        if (!date) return;
        const currentStart = timestampDate(instance.start.value);
        const currentEnd = timestampDate(instance.end.value);
        if (!currentStart || currentEnd) {
          instance.start.value = String(dayStart(date));
          instance.end.value = '';
        } else if (dayStart(date) < dayStart(currentStart)) {
          instance.start.value = String(dayStart(date));
          instance.end.value = String(dayStart(currentStart));
        } else {
          instance.end.value = String(dayStart(date));
          close(instance);
        }
        sync(instance);
        render(instance);
      });
    });
  }

  document.addEventListener('click', event => {
    document.querySelectorAll('[data-project-date-range] .project-date-range-popover:not([hidden])').forEach(popover => {
      const root = popover.closest('[data-project-date-range]');
      const instance = instances.get(root);
      if (instance && !root.contains(event.target)) close(instance);
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('[data-project-date-range] .project-date-range-popover:not([hidden])').forEach(popover => close(instances.get(popover.closest('[data-project-date-range]'))));
  });

  window.GWProjectDateRange = {bind, setRange(container, startValue, endValue) {
    const instance = typeof container === 'string' ? document.querySelector(container) : container;
    const state = instances.get(instance);
    if (!state) return;
    state.start.value = Number(startValue || 0) > 0 ? String(startValue) : '';
    state.end.value = Number(endValue || 0) > 0 ? String(endValue) : '';
    const date = timestampDate(state.start.value);
    if (date) state.month = new Date(date.getFullYear(), date.getMonth(), 1);
    sync(state);
    render(state);
  }};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => bind()); else bind();
})(window, document);
