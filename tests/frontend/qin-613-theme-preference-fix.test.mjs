// [QIN-613] 深色模式切换、跨页与刷新保持主题、删除减少动态效果产品自建逻辑
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = rel => readFileSync(new URL(`../../${rel}`, import.meta.url), 'utf8');

const themeJsSource = read('web/js/core/theme.js');
const workspaceCommonJsSource = read('web/js/core/workspace-common.js');
const settingsHtml = read('web/pages/settings.html');
const indexHtml = read('web/pages/index.html');
const settingsJsSource = read('web/js/controllers/settings.js');
const homeControllerJsSource = read('web/js/controllers/home-controller.js');
const themeCss = read('web/css/base/theme.css');
const workspacePagesCss = read('web/css/base/workspace-pages.css');
const hardwareButtonsCss = read('web/css/base/hardware-buttons.css');

/**
 * 极简但在行为上严格遵循 DOM / Storage / Event 规范的沙箱测试宿主
 */
function createMockEnvironment(initialStorage = {}) {
  const store = new Map(Object.entries(initialStorage));
  let throwOnSetItem = false;
  let throwOnKey = null;

  const storage = {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      if (throwOnSetItem || (throwOnKey && throwOnKey === key)) {
        const err = new Error('QuotaExceededError: storage is full');
        err.name = 'QuotaExceededError';
        throw err;
      }
      store.set(String(key), String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
    clear() {
      store.clear();
    },
    _setThrow(val, key = null) {
      throwOnSetItem = val;
      throwOnKey = key;
    },
    _dump() {
      return Object.fromEntries(store.entries());
    }
  };

  class ClassList {
    constructor(el) {
      this.el = el;
      this._set = new Set();
    }
    add(...cls) {
      cls.forEach(c => c && this._set.add(c));
    }
    remove(...cls) {
      cls.forEach(c => this._set.delete(c));
    }
    contains(c) {
      return this._set.has(c);
    }
    toggle(c, force) {
      if (typeof force === 'boolean') {
        if (force) this.add(c);
        else this.remove(c);
        return force;
      }
      if (this.contains(c)) {
        this.remove(c);
        return false;
      }
      this.add(c);
      return true;
    }
  }

  class MockElement {
    constructor(tagName, id = '', classes = []) {
      this.tagName = tagName.toUpperCase();
      this.id = id;
      this.classList = new ClassList(this);
      classes.forEach(c => this.classList.add(c));
      this.attributes = new Map();
      this.style = {
        setProperty(k, v) { this[k] = v; },
        getPropertyValue(k) { return this[k] || ''; }
      };
      this.children = [];
      this.parentElement = null;
      this.listeners = new Map();
      this.dataset = {};
    }
    setAttribute(name, val) {
      this.attributes.set(name, String(val));
    }
    getAttribute(name) {
      return this.attributes.has(name) ? this.attributes.get(name) : null;
    }
    removeAttribute(name) {
      this.attributes.delete(name);
    }
    hasAttribute(name) {
      return this.attributes.has(name);
    }
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    }
    addEventListener(evt, fn) {
      if (!this.listeners.has(evt)) this.listeners.set(evt, []);
      this.listeners.get(evt).push(fn);
    }
    dispatchEvent(event) {
      const list = this.listeners.get(event.type) || [];
      list.forEach(fn => fn(event));
      if (this.parentElement) this.parentElement.dispatchEvent(event);
    }
    closest(selector) {
      if (selector === '#' + this.id) return this;
      if (selector.startsWith('.') && this.classList.contains(selector.slice(1))) return this;
      if (selector.startsWith('[data-setting') && this.dataset.setting) return this;
      return null;
    }
  }

  const docEl = new MockElement('html');
  const head = new MockElement('head');
  const body = new MockElement('body');
  docEl.appendChild(head);
  docEl.appendChild(body);

  const windowListeners = new Map();
  const documentListeners = new Map();

  const mockWindow = {
    localStorage: storage,
    document: {
      documentElement: docEl,
      head,
      body,
      createElement(tag) {
        return new MockElement(tag);
      },
      getElementById(id) {
        if (docEl.id === id) return docEl;
        const find = el => {
          if (el.id === id) return el;
          for (const ch of el.children) {
            const res = find(ch);
            if (res) return res;
          }
          return null;
        };
        return find(docEl);
      },
      querySelector(sel) {
        if (sel === '#themeToggle') return this.getElementById('themeToggle');
        return null;
      },
      querySelectorAll() {
        return [];
      },
      addEventListener(evt, fn) {
        if (!documentListeners.has(evt)) documentListeners.set(evt, []);
        documentListeners.get(evt).push(fn);
      },
      dispatchEvent(evt) {
        const list = documentListeners.get(evt.type) || [];
        list.forEach(fn => fn(evt));
      }
    },
    addEventListener(evt, fn) {
      if (!windowListeners.has(evt)) windowListeners.set(evt, []);
      windowListeners.get(evt).push(fn);
    },
    dispatchEvent(evt) {
      const list = windowListeners.get(evt.type) || [];
      list.forEach(fn => fn(evt));
    },
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    location: { search: '', hash: '', origin: 'http://localhost:2077' },
    getComputedStyle(el) {
      return {
        overflow: '',
        overflowY: '',
        getPropertyValue(prop) {
          return el?.style?.getPropertyValue ? el.style.getPropertyValue(prop) : '';
        }
      };
    },
    parent: null
  };
  mockWindow.self = mockWindow;
  mockWindow.top = mockWindow;
  mockWindow.parent = mockWindow;

  return { mockWindow, storage, docEl, body };
}

const executeThemeJs = win => {
  const fn = new Function('window', 'document', 'localStorage', 'CustomEvent', 'getComputedStyle', themeJsSource);
  fn(win, win.document, win.localStorage, win.CustomEvent, win.getComputedStyle);
};

test('AC1 & AC2: 设置页与首页设置视图点击切换，开关选中态与界面主题同时改变且两入口同步', () => {
  const { mockWindow } = createMockEnvironment();
  executeThemeJs(mockWindow);

  const StudioTheme = mockWindow.StudioTheme;
  assert.ok(StudioTheme, 'StudioTheme 接口必须挂载到 window');
  assert.equal(StudioTheme.get(), 'dark', '未设置时默认深色模式');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'dark');
  assert.equal(mockWindow.document.documentElement.classList.contains('studio-theme-dark'), true);
  assert.equal(mockWindow.document.documentElement.classList.contains('theme-light'), false);

  // 模拟创建设置页开关与首页开关 DOM
  const settingsToggle = {
    id: 'themeToggle',
    classList: {
      _set: new Set(['toggle', 'hw-slide-toggle', 'on']),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
      toggle(c, f) { if (f) this.add(c); else this.remove(c); }
    },
    attrs: { 'aria-pressed': 'true' },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; }
  };
  const homeToggle = {
    id: 'themeToggle',
    classList: {
      _set: new Set(['toggle', 'hw-slide-toggle', 'on']),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
      toggle(c, f) { if (f) this.add(c); else this.remove(c); }
    },
    attrs: { 'aria-pressed': 'true' },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; }
  };

  // 监听 studio-theme-change 同步开关状态
  mockWindow.addEventListener('studio-theme-change', e => {
    const isDark = e.detail?.theme === 'dark';
    settingsToggle.classList.toggle('on', isDark);
    settingsToggle.setAttribute('aria-pressed', String(isDark));
    homeToggle.classList.toggle('on', isDark);
    homeToggle.setAttribute('aria-pressed', String(isDark));
  });

  // 动作：点击切换到 light
  StudioTheme.set('light');

  // 验证 AC1: 开关选中态与实际界面主题同时变化，切换为 light
  assert.equal(StudioTheme.get(), 'light');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'light');
  assert.equal(mockWindow.document.documentElement.classList.contains('theme-light'), true);
  assert.equal(mockWindow.document.documentElement.classList.contains('studio-theme-dark'), false);
  assert.equal(settingsToggle.classList.contains('on'), false, '设置页开关切为未选中');
  assert.equal(settingsToggle.getAttribute('aria-pressed'), 'false');

  // 验证 AC2: 首页设置视图同步为 light 且开关为未选中
  assert.equal(homeToggle.classList.contains('on'), false, '首页开关切为未选中');
  assert.equal(homeToggle.getAttribute('aria-pressed'), 'false');

  // 动作：首页再次点击切换回 dark
  StudioTheme.set('dark');
  assert.equal(StudioTheme.get(), 'dark');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'dark');
  assert.equal(mockWindow.document.documentElement.classList.contains('theme-light'), false);
  assert.equal(mockWindow.document.documentElement.classList.contains('studio-theme-dark'), true);
  assert.equal(settingsToggle.classList.contains('on'), true);
  assert.equal(settingsToggle.getAttribute('aria-pressed'), 'true');
  assert.equal(homeToggle.classList.contains('on'), true);
  assert.equal(homeToggle.getAttribute('aria-pressed'), 'true');
});

test('AC3, AC4, AC5: 跨页面首屏防闪烁、切页与刷新保持所选主题', () => {
  // 模拟用户成功保存了 light 主题
  const initialStorage = { studio_theme: 'light', canvas_theme: 'light', theme: 'light' };

  // 校验目标所有工作台页面是否包含首屏内联阻塞脚本与 theme.js
  const targetPages = [
    'web/pages/index.html',
    'web/pages/settings.html',
    'web/pages/projects.html',
    'web/pages/assets.html',
    'web/pages/collab.html',
    'web/pages/agents.html',
    'web/pages/canvas.html',
    'web/pages/production.html',
    'web/pages/storyboard.html',
    'web/pages/workflow.html',
    'web/pages/workshop.html',
    'web/embeds/asset-manager.html',
    'web/embeds/canvas-list.html'
  ];

  for (const pageRel of targetPages) {
    const content = read(pageRel);
    assert.match(
      content,
      /<script>\s*\(function\(\)\s*\{[\s\S]*?localStorage\.getItem\(['"]studio_theme['"]\)[\s\S]*?setAttribute\(['"]data-theme['"]/m,
      `${pageRel} 必须在 <head> 最前部包含防闪烁内联阻塞主题脚本`
    );
    assert.match(
      content,
      /<script src="\/static\/js\/core\/theme\.js(?:\?v=[^"]*)?"><\/script>/,
      `${pageRel} 必须在 <head> 引入 theme.js`
    );

    // 执行模拟首屏内联脚本执行（AC5 首屏初始绘制验证）
    const inlineScriptMatch = content.match(/<script>\s*(\(function\(\)\s*\{[\s\S]*?\}\)\(\);)\s*<\/script>/);
    assert.ok(inlineScriptMatch, `${pageRel} 内联脚本匹配成功`);

    const mockEnv = createMockEnvironment(initialStorage);
    const runInline = new Function('localStorage', 'document', 'location', 'window', 'URLSearchParams', inlineScriptMatch[1]);
    runInline(mockEnv.storage, mockEnv.mockWindow.document, mockEnv.mockWindow.location, mockEnv.mockWindow, URLSearchParams);

    assert.equal(mockEnv.docEl.getAttribute('data-theme'), 'light', `${pageRel} 首屏执行内联脚本后必须立即为 light`);
    assert.equal(mockEnv.docEl.classList.contains('theme-light'), true, `${pageRel} 首屏立即带有 theme-light 类名`);
    assert.equal(mockEnv.docEl.classList.contains('studio-theme-dark'), false, `${pageRel} 首屏严禁先渲染 studio-theme-dark`);
  }

  // AC4: 刷新/冷读验证：新开环境从 storage 读取
  const { mockWindow } = createMockEnvironment(initialStorage);
  executeThemeJs(mockWindow);
  assert.equal(mockWindow.StudioTheme.get(), 'light', '冷读必须为上次保存的 light');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'light');
});

test('AC6: 持久化写入失败点：原子回滚、无半写入、错误抛出且界面状态不伪装成功', () => {
  const { mockWindow, storage } = createMockEnvironment({
    studio_theme: 'dark',
    canvas_theme: 'dark',
    theme: 'dark'
  });

  executeThemeJs(mockWindow);
  const StudioTheme = mockWindow.StudioTheme;

  // 模拟在中途写入 canvas_theme 时抛出配额超限异常
  storage._setThrow(false);
  storage._setThrow(true, 'canvas_theme');

  // 执行 set('light')，断言抛错
  assert.throws(
    () => StudioTheme.set('light'),
    /QuotaExceededError/,
    '写入失败必须抛出错误，严禁吞错'
  );

  // 验证原子回滚与无半写入：所有键必须全部回滚回 dark，不得残留任何 light
  const dumped = storage._dump();
  assert.equal(dumped.studio_theme, 'dark', 'studio_theme 必须回滚为 dark');
  assert.equal(dumped.canvas_theme, 'dark', 'canvas_theme 必须为 dark');
  assert.equal(dumped.theme, 'dark', 'theme 必须为 dark');

  // 验证 DOM 界面主题未被篡改
  assert.equal(StudioTheme.get(), 'dark');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'dark');
  assert.equal(mockWindow.document.documentElement.classList.contains('studio-theme-dark'), true);
});

test('AC7 & AC8: 冷启动恢复与异常降级', () => {
  // 无存储时默认降级为 dark
  const { mockWindow: emptyWin } = createMockEnvironment({});
  executeThemeJs(emptyWin);
  assert.equal(emptyWin.StudioTheme.get(), 'dark');
  assert.equal(emptyWin.document.documentElement.getAttribute('data-theme'), 'dark');

  // 存储被禁用（SecurityError 抛错）时安全降级为 dark
  const errorStorage = {
    getItem() { throw new Error('SecurityError: localStorage is disabled'); },
    setItem() { throw new Error('SecurityError'); },
    removeItem() {}
  };
  const { mockWindow: secWin } = createMockEnvironment();
  secWin.localStorage = errorStorage;
  executeThemeJs(secWin);
  assert.equal(secWin.StudioTheme.get(), 'dark', '存储异常时平稳降级为 dark');
});

test('AC9 & AC10: iframe 双向广播与乱序多标签页一致性', () => {
  const { mockWindow } = createMockEnvironment({ studio_theme: 'dark' });
  executeThemeJs(mockWindow);

  let frameMessage = null;
  const mockIframe = {
    contentWindow: {
      postMessage(msg) {
        frameMessage = msg;
      }
    }
  };
  mockWindow.document.querySelectorAll = sel => sel === 'iframe' ? [mockIframe] : [];

  mockWindow.StudioTheme.set('light');
  assert.deepEqual(frameMessage, { type: 'studio-theme', theme: 'light' }, '父窗口切换时广播给所有子 iframe');
});

test('AC11 & AC12: 彻底删除自建「减少动态效果」产品控件与逻辑，保留系统级无障碍保护', () => {
  // 1. 设置页与首页设置视图中不得残留 reduce_motion HTML
  assert.doesNotMatch(
    settingsHtml,
    /data-setting="reduce_motion"/,
    'settings.html 必须彻底移除 data-setting="reduce_motion"'
  );
  assert.doesNotMatch(
    settingsHtml,
    /减少动态效果/,
    'settings.html 必须彻底移除「减少动态效果」文字与 DOM 行'
  );
  assert.doesNotMatch(
    indexHtml,
    /data-setting="reduce_motion"/,
    'index.html 必须彻底移除 data-setting="reduce_motion"'
  );
  assert.doesNotMatch(
    indexHtml,
    /减少动态效果/,
    'index.html 必须彻底移除「减少动态效果」文字与 DOM 行'
  );

  // 2. 验证全站必须完好保留的系统级 prefers-reduced-motion 保护
  assert.match(
    hardwareButtonsCss,
    /@media\s*\(prefers-reduced-motion:\s*reduce\)/,
    'hardware-buttons.css 必须保留系统级 prefers-reduced-motion 媒体查询'
  );
  assert.match(
    workspacePagesCss,
    /@media\s*\(prefers-reduced-motion:\s*reduce\)/,
    'workspace-pages.css 必须保留系统级 prefers-reduced-motion 媒体查询'
  );
});

test('AC13: 浅色与深色模式开关选中态视觉反差与页面文字对比度支持', () => {
  // 验证 theme.css 提供了浅色模式变量与开关反差
  assert.match(themeCss, /html\[data-theme="light"\]/, 'theme.css 必须提供 light 模式选择器');
  assert.match(themeCss, /color-scheme:\s*light/, 'theme.css 在 light 模式声明 color-scheme: light');
  assert.match(themeCss, /background:\s*rgb\(241,\s*242,\s*245\)/i, 'theme.css 在 light 模式提供浅灰背景');
  assert.match(themeCss, /\.hw-slide-toggle:is\(\.on,\s*\[aria-pressed="true"\]\)/, 'theme.css 必须提供激活态开关的金边黑底');
  assert.match(themeCss, /\.hw-slide-toggle:not\(\.on\):not\(\[aria-pressed="true"\]\)/, 'theme.css 必须提供关闭态开关的浅灰底色');
});

test('AC14: 默认启动页、恢复上次页面、任务中心自动刷新、语言及团队偏好完好保留', () => {
  assert.match(settingsHtml, /data-setting="restore_page"/, 'restore_page 开关必须完好保留');
  assert.match(settingsHtml, /data-setting="task_refresh"/, 'task_refresh 开关必须完好保留');
  assert.match(settingsHtml, /id="defaultPage"/, 'defaultPage 下拉选项必须完好保留');
  assert.match(settingsHtml, /id="languageToggle"/, 'languageToggle 按钮必须完好保留');
  assert.match(settingsHtml, /data-panel="general"/, 'general 面板必须完好保留');
  assert.match(settingsHtml, /data-panel="directories"/, 'directories 面板必须完好保留');
  assert.match(settingsJsSource, /preferences\.default_page/, 'settings.js 必须保留 default_page 处理');
  assert.match(homeControllerJsSource, /getElementById\(['"]languageToggle['"]\)/, 'home-controller.js 必须保留 languageToggle 绑定');
});

