import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const controller = readFileSync('web/js/controllers/storyboard-controller.js', 'utf8');

test('分镜页控制器不再包含 DEMO_PREVIEW 与硬编码示例数据', () => {
  assert.doesNotMatch(controller, /DEMO_PREVIEW/);
  assert.doesNotMatch(controller, /sb-01/);
  assert.doesNotMatch(controller, /SC03_SH01/);
  assert.doesNotMatch(controller, /钛金母体神殿/);
  assert.doesNotMatch(controller, /钛金圣殿对峙/);
  assert.doesNotMatch(controller, /序幕起航/);
  assert.doesNotMatch(controller, /黎明信标/);
});

test('分镜页控制器声明未接入降级标记与默认空状态', () => {
  assert.match(controller, /data-gw-degradation="not_integrated"/);
  assert.match(controller, /function notIntegratedMarkup\(/);
  assert.match(controller, /function renderNotIntegratedState\(\)/);
  assert.match(controller, /shots:\s*\[\]/);
  assert.match(controller, /scenes:\s*\[\]/);
  assert.match(controller, /分镜数据未接入/);
  assert.match(controller, /场次数据未接入/);
});

test('分镜控制器运行时在无真实数据时展示明确「未接入」空态，不伪造镜头', () => {
  // 构造轻量 DOM 替身模拟测试环境
  const elements = {};
  const createElement = (tag, id) => {
    const el = {
      tagName: tag.toUpperCase(),
      id: id || '',
      innerHTML: '',
      textContent: '',
      dataset: {},
      style: {},
      contains: () => false,
      querySelectorAll: () => [],
      querySelector: () => null,
      setAttribute: () => {},
      removeAttribute: () => {}
    };
    if (id) elements[id] = el;
    return el;
  };

  const doc = {
    readyState: 'complete',
    getElementById: id => elements[id] || null,
    querySelector: sel => {
      if (sel === '#storyboardSceneNavContainer .font-mono.text-\\[\\#dfc384\\]') {
        return elements['navCountBadge'] || null;
      }
      return null;
    },
    querySelectorAll: () => [],
    addEventListener: () => {}
  };

  createElement('div', 'storyboardGrid');
  createElement('div', 'storyboardSceneNav');
  createElement('h2', 'storyboardTitle');
  createElement('span', 'navCountBadge');

  let alertMessage = '';
  const mockWindow = {
    location: { search: '' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: doc,
    alert: msg => { alertMessage = msg; },
    lucide: { createIcons: () => {} },
    URLSearchParams: globalThis.URLSearchParams
  };
  mockWindow.window = mockWindow;

  const context = vm.createContext(mockWindow);
  vm.runInContext(controller, context);

  const storyboard = mockWindow.WorkbenchStoryboard;
  assert.ok(storyboard, 'WorkbenchStoryboard 必须挂载到 window');

  // 初始化后无数据状态验证
  assert.match(elements.storyboardGrid.innerHTML, /data-gw-degradation="not_integrated"/);
  assert.match(elements.storyboardGrid.innerHTML, /分镜数据未接入/);
  assert.match(elements.storyboardSceneNav.innerHTML, /data-gw-degradation="not_integrated"/);
  assert.match(elements.storyboardSceneNav.innerHTML, /场次数据未接入/);
  assert.match(elements.storyboardTitle.textContent, /分镜数据未接入/);

  // 尝试在空状态下追加镜头：不得伪造数据
  storyboard.addNewShot();
  assert.match(elements.storyboardGrid.innerHTML, /分镜数据未接入/);
  assert.match(alertMessage, /未接入/);

  // 真实数据注入路径验证：真实数据可正常渲染
  const realShots = [
    {
      id: 'real-shot-001',
      code: 'SC01_SH001',
      title: '真实测试镜头 1',
      shotType: '全景 · 35mm',
      dialogue: '这是真实分镜对白内容',
      duration: '4.0s',
      img: 'https://example.com/shot1.jpg',
      status: 'ready'
    }
  ];
  storyboard.setShots(realShots);

  assert.doesNotMatch(elements.storyboardGrid.innerHTML, /data-gw-degradation="not_integrated"/);
  assert.match(elements.storyboardGrid.innerHTML, /data-storyboard-shot="real-shot-001"/);
  assert.match(elements.storyboardGrid.innerHTML, /真实测试镜头 1/);
  assert.match(elements.storyboardGrid.innerHTML, /这是真实分镜对白内容/);
  assert.match(elements.storyboardTitle.textContent, /共 1 个分镜/);
});
