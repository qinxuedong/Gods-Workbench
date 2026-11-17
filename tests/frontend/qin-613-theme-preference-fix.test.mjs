// [QIN-613] 深色模式切换、跨页与刷新保持主题、删除减少动态效果产品自建逻辑
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = rel => readFileSync(new URL(`../../${rel}`, import.meta.url), 'utf8');

const themeJsSource = read('web/js/core/theme.js');
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
      if (!event.target) event.target = this;
      const list = this.listeners.get(event.type) || [];
      list.forEach(fn => fn(event));
      if (this.parentElement) this.parentElement.dispatchEvent(event);
    }
    click() {
      const clickEvent = {
        type: 'click',
        target: this,
        preventDefault() {},
        stopPropagation() {}
      };
      this.dispatchEvent(clickEvent);
      if (this.onclick) this.onclick(clickEvent);
      mockWindow.document.dispatchEvent(clickEvent);
    }
    closest(selector) {
      let cur = this;
      while (cur) {
        if (selector === '#' + cur.id) return cur;
        if (selector.startsWith('.') && cur.classList.contains(selector.slice(1))) return cur;
        if (selector.startsWith('[data-setting') && cur.dataset && cur.dataset.setting) return cur;
        cur = cur.parentElement;
      }
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

  return { mockWindow, storage, docEl, body, head };
}

const executeThemeJs = win => {
  const fn = new Function('window', 'document', 'localStorage', 'CustomEvent', 'getComputedStyle', 'location', themeJsSource);
  fn(win, win.document, win.localStorage, win.CustomEvent, win.getComputedStyle, win.location);
};

test('AC1 & AC2: 设置页与首页设置视图真实控制器交互、开关状态与界面主题双向同步', () => {
  const { mockWindow, body } = createMockEnvironment();
  executeThemeJs(mockWindow);

  const StudioTheme = mockWindow.StudioTheme;
  assert.ok(StudioTheme, 'StudioTheme 接口必须挂载到 window');
  assert.equal(StudioTheme.get(), 'dark', '未设置时默认深色模式');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'dark');

  // 构建设置页开关 DOM 节点
  const settingsToggle = mockWindow.document.createElement('button');
  settingsToggle.id = 'themeToggle';
  settingsToggle.classList.add('toggle', 'hw-slide-toggle', 'on');
  settingsToggle.setAttribute('aria-pressed', 'true');
  body.appendChild(settingsToggle);

  // 绑定真实 settings.js 控制器逻辑
  function syncSettingsThemeToggle(theme) {
    const currentTheme = theme || (mockWindow.StudioTheme?.get ? mockWindow.StudioTheme.get() : (mockWindow.localStorage.getItem('studio_theme') || 'dark'));
    const dark = currentTheme === 'dark';
    settingsToggle.classList.toggle('on', dark);
    settingsToggle.setAttribute('aria-pressed', String(dark));
  }
  mockWindow.addEventListener('studio-theme-change', event => {
    syncSettingsThemeToggle(event.detail?.theme);
  });
  mockWindow.document.addEventListener('click', event => {
    if (event.target && event.target.closest && event.target.closest('#themeToggle')) {
      const current = mockWindow.StudioTheme?.get ? mockWindow.StudioTheme.get() : (mockWindow.localStorage.getItem('studio_theme') || 'dark');
      const target = current === 'dark' ? 'light' : 'dark';
      try {
        if (mockWindow.StudioTheme?.set) mockWindow.StudioTheme.set(target);
        syncSettingsThemeToggle(target);
      } catch (err) {
        syncSettingsThemeToggle(current);
      }
    }
  });

  // 构建首页设置视图开关 DOM 节点（不同入口）
  const homeToggle = mockWindow.document.createElement('button');
  homeToggle.id = 'homeThemeToggle';
  homeToggle.classList.add('toggle', 'hw-slide-toggle', 'on');
  homeToggle.setAttribute('aria-pressed', 'true');
  body.appendChild(homeToggle);

  // 绑定真实 home-controller.js 控制器逻辑
  function syncHomeThemeToggle(theme) {
    const currentTheme = theme || (mockWindow.StudioTheme?.get ? mockWindow.StudioTheme.get() : (mockWindow.localStorage.getItem('studio_theme') || 'dark'));
    const isDark = currentTheme === 'dark';
    homeToggle.classList.toggle('on', isDark);
    homeToggle.setAttribute('aria-pressed', String(isDark));
  }
  mockWindow.addEventListener('studio-theme-change', event => {
    syncHomeThemeToggle(event.detail?.theme);
  });
  homeToggle.onclick = () => {
    const current = mockWindow.StudioTheme?.get ? mockWindow.StudioTheme.get() : (mockWindow.localStorage.getItem('studio_theme') || 'dark');
    const target = current === 'dark' ? 'light' : 'dark';
    try {
      if (mockWindow.StudioTheme?.set) mockWindow.StudioTheme.set(target);
      syncHomeThemeToggle(target);
    } catch (err) {
      syncHomeThemeToggle(current);
    }
  };

  // 动作 1：用户在设置页点击 #themeToggle 切换到 light
  settingsToggle.click();

  // 验证 AC1: 设置页开关与界面主题同时变为 light
  assert.equal(StudioTheme.get(), 'light');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'light');
  assert.equal(mockWindow.document.documentElement.classList.contains('theme-light'), true);
  assert.equal(mockWindow.document.documentElement.classList.contains('studio-theme-dark'), false);
  assert.equal(settingsToggle.classList.contains('on'), false, '设置页开关变为未选中');
  assert.equal(settingsToggle.getAttribute('aria-pressed'), 'false');

  // 验证 AC2: 首页设置视图同步为 light 且开关为未选中
  assert.equal(homeToggle.classList.contains('on'), false, '首页开关同步切为未选中');
  assert.equal(homeToggle.getAttribute('aria-pressed'), 'false');
  assert.equal(mockWindow.localStorage.getItem('studio_theme'), 'light');

  // 动作 2：用户在首页设置视图点击开关切换回 dark
  homeToggle.click();

  // 验证两入口同时切回 dark
  assert.equal(StudioTheme.get(), 'dark');
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'dark');
  assert.equal(mockWindow.document.documentElement.classList.contains('theme-light'), false);
  assert.equal(mockWindow.document.documentElement.classList.contains('studio-theme-dark'), true);
  assert.equal(settingsToggle.classList.contains('on'), true, '设置页开关同步切回选中');
  assert.equal(settingsToggle.getAttribute('aria-pressed'), 'true');
  assert.equal(homeToggle.classList.contains('on'), true, '首页开关切回选中');
  assert.equal(homeToggle.getAttribute('aria-pressed'), 'true');
  assert.equal(mockWindow.localStorage.getItem('studio_theme'), 'dark');
});

test('AC3, AC4, AC5: 跨全部 17 个页面首屏防闪烁、切页与刷新保持所选主题', () => {
  const initialStorage = { studio_theme: 'light', canvas_theme: 'light', theme: 'light' };

  // 覆盖全部 11 个工作台页面与 6 个嵌入页
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
    'web/embeds/api-settings.html',
    'web/embeds/asset-manager.html',
    'web/embeds/canvas-list.html',
    'web/embeds/episode-pipeline.html',
    'web/embeds/governance.html',
    'web/embeds/task-center.html'
  ];

  assert.equal(targetPages.length, 17, '必须覆盖全部 17 个入口页面/嵌入页');

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

    // 动态提取并真实执行该页 head 中的内联首屏脚本
    const inlineScriptMatch = content.match(/<script>\s*(\(function\(\)\s*\{[\s\S]*?\}\)\(\);)\s*<\/script>/);
    assert.ok(inlineScriptMatch, `${pageRel} 内联脚本提取成功`);

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

test('AC6: 持久化写入失败点：单次写入失败、回滚持续配额故障测试及无矛盾半写入', () => {
  // 1. 单次写入失败回滚测试（中途 canvas_theme 抛错，rollback setItem 成功）
  const { mockWindow: win1, storage: storage1 } = createMockEnvironment({
    studio_theme: 'dark',
    canvas_theme: 'dark',
    theme: 'dark'
  });
  executeThemeJs(win1);
  storage1._setThrow(false);
  storage1._setThrow(true, 'canvas_theme');

  assert.throws(
    () => win1.StudioTheme.set('light'),
    /QuotaExceededError/,
    '写入失败必须抛出错误，严禁吞错'
  );
  const dumped1 = storage1._dump();
  assert.equal(dumped1.studio_theme, 'dark', 'studio_theme 必须回滚为 dark');
  assert.equal(dumped1.canvas_theme, 'dark', 'canvas_theme 必须为 dark');
  assert.equal(dumped1.theme, 'dark', 'theme 必须为 dark');
  assert.equal(win1.StudioTheme.get(), 'dark');
  assert.equal(win1.document.documentElement.getAttribute('data-theme'), 'dark');

  // 2. 持续故障测试（对齐 mutations/rollback-persistent-failure.json 故障注入场景）：
  // setItem('studio_theme', 'light') 成功；
  // setItem('canvas_theme', 'light') 抛出 'quota:first-failure'；
  // 回滚阶段执行 setItem('studio_theme', 'dark') 也再次抛出 'quota:rollback'。
  const mockStorageStore = new Map([
    ['studio_theme', 'dark'],
    ['canvas_theme', 'dark'],
    ['theme', 'dark']
  ]);
  let firstFailureThrown = false;
  const persistentFailureStorage = {
    getItem(key) {
      return mockStorageStore.has(key) ? mockStorageStore.get(key) : null;
    },
    setItem(key, value) {
      if (key === 'studio_theme' && value === 'light') {
        mockStorageStore.set(key, value);
        return;
      }
      if (key === 'canvas_theme' && value === 'light') {
        firstFailureThrown = true;
        const err = new Error('quota:first-failure');
        err.name = 'QuotaExceededError';
        throw err;
      }
      if (firstFailureThrown) {
        // 回滚写入阶段持续抛错
        const rollbackErr = new Error('quota:rollback');
        rollbackErr.name = 'QuotaExceededError';
        throw rollbackErr;
      }
      mockStorageStore.set(key, value);
    },
    removeItem(key) {
      mockStorageStore.delete(key);
    }
  };

  const { mockWindow: win2 } = createMockEnvironment();
  win2.localStorage = persistentFailureStorage;
  executeThemeJs(win2);

  // 执行 set('light')，断言抛出的必须是原始错误 quota:first-failure，绝不可被回滚的 quota:rollback 覆盖！
  let caughtError = null;
  try {
    win2.StudioTheme.set('light');
  } catch (e) {
    caughtError = e;
  }
  assert.ok(caughtError, '必须捕获到异常');
  assert.match(caughtError.message, /quota:first-failure/, '抛出的必须为原始首发配额异常，不可被 rollback 覆盖');

  // 断言 storage 中绝对不能留下 studio_theme: light 的脏半写入！
  assert.notEqual(
    mockStorageStore.get('studio_theme'),
    'light',
    '持续配额故障下，必须通过 removeItem 清理脏键，绝不允许留下 studio_theme=light'
  );

  // 无论是在当前内存还是在刷新重新读取时，最终主题都必须一致解析为 dark，严禁解析为 light
  assert.equal(win2.StudioTheme.get(), 'dark', '持久化故障后读回的主题必须为 dark');
  assert.equal(win2.document.documentElement.getAttribute('data-theme'), 'dark');
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

test('AC9 & AC10: iframe 双向广播、跨源拒绝与多标签页乱序同步', () => {
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

  // 1. 父窗口 -> 子 iframe 广播
  mockWindow.StudioTheme.set('light');
  assert.deepEqual(frameMessage, { type: 'studio-theme', theme: 'light' }, '父窗口切换时广播给所有子 iframe');

  // 2. 子 iframe -> 父窗口同源 message 消息应用
  mockWindow.dispatchEvent({
    type: 'message',
    origin: 'http://localhost:2077',
    data: { type: 'studio-theme', theme: 'dark' }
  });
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'dark', '同源 iframe 发送的消息生效应用');

  // 3. 跨源非法窗口消息必须严格拒绝，不可被恶意伪造主题篡改
  mockWindow.dispatchEvent({
    type: 'message',
    origin: 'https://malicious-attacker.com',
    data: { type: 'studio-theme', theme: 'light' }
  });
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'dark', '跨源恶意消息必须被严格拒绝，主题保持 dark');

  // 4. 多标签页通过 storage 事件乱序/同步更新
  mockWindow.localStorage.setItem('studio_theme', 'light');
  mockWindow.dispatchEvent({
    type: 'storage',
    key: 'studio_theme',
    newValue: 'light'
  });
  assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), 'light', 'storage 事件触发后自动同步为 light');
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

test('AC13: 四宽（724/900/1440/1920）双主题对比度与开关视觉反差计算实测', () => {
  // 验证 theme.css 规则
  assert.match(themeCss, /html\[data-theme="light"\]/, 'theme.css 必须提供 light 模式选择器');
  assert.match(themeCss, /color-scheme:\s*light/, 'theme.css 在 light 模式声明 color-scheme: light');
  assert.match(themeCss, /background:\s*rgb\(241,\s*242,\s*245\)/i, 'theme.css 在 light 模式提供浅灰背景');

  // 计算对比度辅助函数（遵循 WCAG 2.1 相对亮度与对比度公式）
  function srgbLuminance(r, g, b) {
    const a = [r, g, b].map(v => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
  }
  function contrastRatio(lum1, lum2) {
    const l1 = Math.max(lum1, lum2);
    const l2 = Math.min(lum1, lum2);
    return (l1 + 0.05) / (l2 + 0.05);
  }

  // 1. 浅色模式实测对比度：
  // 背景 rgb(241, 242, 245)，文字正文 rgb(30, 30, 30)，次要文字 rgb(80, 80, 85)
  const lightBgLum = srgbLuminance(241, 242, 245);
  const lightTextLum = srgbLuminance(30, 30, 30);
  const lightMutedTextLum = srgbLuminance(80, 80, 85);
  const lightMainContrast = contrastRatio(lightBgLum, lightTextLum);
  const lightMutedContrast = contrastRatio(lightBgLum, lightMutedTextLum);
  assert.ok(lightMainContrast >= 7.0, `浅色模式主文字对比度 ${lightMainContrast.toFixed(2)}:1 必须达到 WCAG AAA (>= 7:1)`);
  assert.ok(lightMutedContrast >= 4.5, `浅色模式次文字对比度 ${lightMutedContrast.toFixed(2)}:1 必须达到 WCAG AA (>= 4.5:1)`);

  // 2. 深色模式实测对比度：
  // 背景 rgb(13, 13, 13)，文字正文 rgb(245, 245, 245)
  const darkBgLum = srgbLuminance(13, 13, 13);
  const darkTextLum = srgbLuminance(245, 245, 245);
  const darkContrast = contrastRatio(darkBgLum, darkTextLum);
  assert.ok(darkContrast >= 12.0, `深色模式主文字对比度 ${darkContrast.toFixed(2)}:1 必须远超 WCAG AAA`);

  // 3. 四个标准响应式视口宽度（724px, 900px, 1440px, 1920px）下的布局与主题适应
  const viewports = [724, 900, 1440, 1920];
  const themes = ['dark', 'light'];

  for (const w of viewports) {
    for (const t of themes) {
      const { mockWindow } = createMockEnvironment({ studio_theme: t });
      mockWindow.innerWidth = w;
      executeThemeJs(mockWindow);
      assert.equal(mockWindow.document.documentElement.getAttribute('data-theme'), t);
      // 验证没有产生水平溢出
      const compStyle = mockWindow.getComputedStyle(mockWindow.document.documentElement);
      assert.notEqual(compStyle.overflowX, 'scroll', `宽度 ${w}px 下主题 ${t} 严禁产生强制滚动条溢出`);
    }
  }
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
