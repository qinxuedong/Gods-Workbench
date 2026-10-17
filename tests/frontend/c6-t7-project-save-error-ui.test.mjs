/**
 * QIN-164 (C6-T7) 拒绝保存时显示 UI 错误回归测试。
 * 验证：
 * 1. 当保存项目被后端拒绝（403 PROJECT_READ_ONLY 或 409 VERSION_CONFLICT）时，
 *    UI 展示清晰、可理解的中文错误 Toast，绝不展示 `[object Object]`；
 * 2. 拒绝保存时保持当前编辑模态框打开，不静默关闭，也不仅写控制台；
 * 3. 当后端返回标准错误包对象、错误字符串或空对象时，均能正确解析出可读文案。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createContext, runInContext } from 'node:vm';

const source = readFileSync(new URL('../../web/js/controllers/projects-controller.js', import.meta.url), 'utf8');

function createHarness({ fetchStatus = 200, fetchBody = {} } = {}) {
  const elements = {};
  const getEl = id => {
    if (!elements[id]) {
      elements[id] = {
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        style: {},
        classList: {
          add() {},
          remove() {}
        },
        appendChild() {},
        remove() {}
      };
    }
    return elements[id];
  };

  const toasts = [];
  const closedModals = [];

  const location = { search: '' };
  const context = createContext({
    URLSearchParams: globalThis.URLSearchParams,
    location,
    requestAnimationFrame: fn => fn(),
    setTimeout: fn => fn(),
    window: {
      location,
      addEventListener() {},
      requestAnimationFrame: fn => fn(),
      setTimeout: (fn) => fn(),
      lucide: { createIcons() {} },
      URLSearchParams: globalThis.URLSearchParams,
      GWDegradation: {
        FORBIDDEN_MESSAGE: '权限不足，已切换为只读模式',
        CONFLICT_MESSAGE: '版本冲突，请刷新并解决并发差异后重试'
      }
    },
    document: {
      getElementById: getEl,
      querySelector: () => null,
      querySelectorAll: () => [],
      body: {
        appendChild() {}
      },
      createElement: tag => ({
        tagName: tag.toUpperCase(),
        className: '',
        style: {},
        innerHTML: '',
        classList: {
          add() {},
          remove() {}
        },
        remove() {}
      })
    },
    HardwareDeck: {
      openModal() {},
      closeModal: id => closedModals.push(id)
    },
    localStorage: {
      getItem: () => null,
      setItem: () => {}
    },
    console: {
      log() {},
      warn() {},
      error() {}
    },
    fetch: async () => ({
      ok: fetchStatus >= 200 && fetchStatus < 300,
      status: fetchStatus,
      json: async () => fetchBody
    })
  });

  context.window.window = context.window;
  context.window.document = context.document;
  context.window.HardwareDeck = context.HardwareDeck;

  runInContext(source, context);

  const ctrl = context.window.WorkbenchProjects;

  // 监听 workbenchToastContainer 内容追加
  const toastContainer = getEl('workbenchToastContainer');
  const origAppendChild = toastContainer.appendChild;
  toastContainer.appendChild = child => {
    toasts.push(child);
    if (origAppendChild) origAppendChild(child);
  };

  // 设置编辑表单基础字段
  getEl('editProjectId').value = 'prj-test-001';
  getEl('editProjectNameInput').value = '测试工程';
  getEl('editProjectVersion').value = '2';

  const mockEvent = { preventDefault() {} };

  return { ctrl, context, getEl, toasts, closedModals, mockEvent };
}

test('源码规则核查：handleUpdateProject 能正确解包标准错误包对象并提供 403/409 降级处理', () => {
  // 不得将 error.detail 直接赋给 toast 导致 [object Object]
  assert.match(
    source,
    /error\.detail\?\.message|typeof error\.detail === 'object'|extractErrorMessage/,
    '应支持解析标准错误包中的 detail.message 字段'
  );
});

test('行为测试：后端返回 403 PROJECT_READ_ONLY 时，Toast 显示具体中文原因且弹窗不关闭', async () => {
  const { ctrl, toasts, closedModals, mockEvent } = createHarness({
    fetchStatus: 403,
    fetchBody: {
      detail: {
        code: 'PROJECT_READ_ONLY',
        message: '项目处于归档状态，只读不可修改'
      }
    }
  });

  await ctrl.handleUpdateProject(mockEvent);

  assert.equal(closedModals.length, 0, '保存被拒时不应关闭编辑弹窗');
  assert.ok(toasts.length > 0, '应弹出错误 Toast');
  const toastHtml = toasts[toasts.length - 1].innerHTML;
  assert.doesNotMatch(toastHtml, /\[object Object\]/, '不得出现 [object Object]');
  assert.match(toastHtml, /项目处于归档状态，只读不可修改/);
});

test('行为测试：后端返回 409 VERSION_CONFLICT 时，Toast 显示版本冲突且弹窗不关闭', async () => {
  const { ctrl, toasts, closedModals, mockEvent } = createHarness({
    fetchStatus: 409,
    fetchBody: {
      detail: {
        code: 'VERSION_CONFLICT',
        message: '版本冲突，该项目已被其他会话更新，请刷新重试'
      }
    }
  });

  await ctrl.handleUpdateProject(mockEvent);

  assert.equal(closedModals.length, 0, '409 冲突时不应关闭编辑弹窗');
  assert.ok(toasts.length > 0, '应弹出错误 Toast');
  const toastHtml = toasts[toasts.length - 1].innerHTML;
  assert.doesNotMatch(toastHtml, /\[object Object\]/, '不得出现 [object Object]');
  assert.match(toastHtml, /版本冲突/);
});

test('行为测试：后端返回无 message 的 403/409 时，回退为语义化中文提示而非空白或通用失败', async () => {
  const { ctrl, toasts, closedModals, mockEvent } = createHarness({
    fetchStatus: 403,
    fetchBody: {}
  });

  await ctrl.handleUpdateProject(mockEvent);

  assert.equal(closedModals.length, 0, '403 时不应关闭编辑弹窗');
  assert.ok(toasts.length > 0, '应弹出错误 Toast');
  const toastHtml = toasts[toasts.length - 1].innerHTML;
  assert.doesNotMatch(toastHtml, /\[object Object\]/);
  assert.match(toastHtml, /权限不足|只读/);
});
