/**
 * Gods' Workbench - 原生公共下拉组件控制器 (GWNativeSelect)
 * 工单：QIN-631 / GitHub #771
 */

(function (global) {
  'use strict';

  /**
   * 增强指定的原生 select 元素
   * @param {HTMLSelectElement|string} target 目标 select 或选择器
   * @param {Object} [options] 配置参数
   * @returns {HTMLElement|null} 外层 wrapper 容器元素
   */
  function enhance(target, options) {
    const opts = options || {};
    let selectEl = null;

    if (typeof target === 'string') {
      selectEl = document.querySelector(target);
    } else if (target && target.nodeType === 1 && target.tagName === 'SELECT') {
      selectEl = target;
    }

    if (!selectEl) {
      return null;
    }

    // 确保内部 select 具备标准类
    selectEl.classList.add('gw-native-select-input');

    // 检查父级是否已经是 wrapper
    let wrapper = selectEl.parentElement;
    if (!wrapper || (!wrapper.classList.contains('gw-select-shell') && !wrapper.classList.contains('select-shell'))) {
      const doc = (selectEl && selectEl.ownerDocument) || (typeof document !== 'undefined' ? document : (opts.document || null));
      if (!doc || typeof doc.createElement !== 'function') {
        return null;
      }
      wrapper = doc.createElement('div');
      wrapper.className = 'gw-select-shell';
      if (wrapper.classList && typeof wrapper.classList.add === 'function') {
        wrapper.classList.add('gw-select-shell');
      }
      if (opts.wrapperClass) {
        wrapper.className += ' ' + opts.wrapperClass;
      }
      if (selectEl.parentNode && typeof selectEl.parentNode.insertBefore === 'function') {
        selectEl.parentNode.insertBefore(wrapper, selectEl);
      }
      if (typeof wrapper.appendChild === 'function') {
        wrapper.appendChild(selectEl);
      }
    } else {
      wrapper.classList.add('gw-select-shell');
    }

    // 幂等事件绑定：仅当未初始化增强时绑定键盘与变更事件（解决预包装控件未绑定事件缺陷）
    const isEnhanced = (selectEl.dataset && selectEl.dataset.gwEnhanced === 'true') ||
      (typeof selectEl.getAttribute === 'function' && selectEl.getAttribute('data-gw-enhanced') === 'true');

    if (!isEnhanced) {
      // 绑定键盘交互：Esc 键失焦并释放焦点（支持返还至触发控件）
      selectEl.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' || e.key === 'Esc') {
          selectEl.blur();
          const trigger = opts.triggerElement || (typeof opts.getTriggerElement === 'function' ? opts.getTriggerElement() : null);
          if (trigger && typeof trigger.focus === 'function') {
            trigger.focus();
          }
        }
      });

      // 监听原生 change 事件，保持状态同步并触发回调
      selectEl.addEventListener('change', function () {
        sync(selectEl);
        if (typeof opts.onChange === 'function') {
          opts.onChange(selectEl.value, selectEl);
        }
      });

      if (selectEl.dataset) {
        selectEl.dataset.gwEnhanced = 'true';
      }
      if (typeof selectEl.setAttribute === 'function') {
        selectEl.setAttribute('data-gw-enhanced', 'true');
      }
      if (wrapper && wrapper.dataset) {
        wrapper.dataset.gwSelectShell = 'true';
      }
      if (wrapper && typeof wrapper.setAttribute === 'function') {
        wrapper.setAttribute('data-gw-select-shell', 'true');
      }
    }

    sync(selectEl);
    return wrapper;
  }

  /**
   * 同步 select 状态至外层容器（如 disabled/loading）
   * @param {HTMLSelectElement} selectEl
   */
  function sync(selectEl) {
    if (!selectEl || !selectEl.parentElement) return;
    const wrapper = selectEl.parentElement;
    if (!wrapper.classList.contains('gw-select-shell') && !wrapper.classList.contains('select-shell')) return;

    if (selectEl.disabled) {
      wrapper.classList.add('is-disabled');
    } else {
      wrapper.classList.remove('is-disabled');
    }

    // 若 options 首项提示加载中，设置 loading 视觉提示
    if (selectEl.options && selectEl.options.length === 1 && /读取中|加载中|等待/i.test(selectEl.options[0].text)) {
      wrapper.classList.add('is-loading');
    } else {
      wrapper.classList.remove('is-loading');
    }
  }

  /**
   * 批量增强指定根节点下的所有标有 [data-gw-select] 的 select
   * @param {HTMLElement|Document} [root]
   */
  function enhanceAll(root) {
    const container = root || (typeof document !== 'undefined' ? document : null);
    if (!container || typeof container.querySelectorAll !== 'function') return;

    const targets = container.querySelectorAll('select[data-gw-select], .gw-select-shell select, select.gw-native-select-input');
    for (let i = 0; i < targets.length; i++) {
      enhance(targets[i]);
    }
  }

  const GWNativeSelect = {
    enhance: enhance,
    enhanceAll: enhanceAll,
    sync: sync
  };

  // 暴露至全局命名空间
  if (typeof global !== 'undefined') {
    global.GWNativeSelect = GWNativeSelect;
  }

  // 浏览器环境下 DOM 就绪后自动初始化声明式控件
  if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () {
        enhanceAll(document);
      });
    } else {
      enhanceAll(document);
    }
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = GWNativeSelect;
  }
})(typeof window !== 'undefined' ? window : globalThis);
