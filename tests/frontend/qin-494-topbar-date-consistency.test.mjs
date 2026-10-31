// QIN-494 (GitHub #502)：顶栏「排期 / 日期」按钮统一使用 hw-btn 机件材质
// 回归门禁：资产库与工作流曾用页面内联 Tailwind 覆盖（纯黑底、小圆角、另一套字号），
// 与其余页面的 hw-btn 口径不一致；这里逐页锁定统一 class，并禁止内联覆盖回流。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const PAGES = [
  'index.html',
  'agents.html',
  'assets.html',
  'collab.html',
  'production.html',
  'projects.html',
  'settings.html',
  'storyboard.html',
  'workflow.html',
  'workshop.html',
];

const readPage = (name) => readFileSync(new URL(`../../web/pages/${name}`, import.meta.url), 'utf8');
const buttonsCss = readFileSync(new URL('../../web/css/base/hardware-buttons.css', import.meta.url), 'utf8');

/** 取出页面里排期按钮的 class 属性值与整段标签。 */
const dateButton = (name) => {
  const html = readPage(name);
  const match = html.match(/<button[^>]*id="masterDeckDate"[^>]*>/);
  assert.ok(match, `${name} 缺少顶栏排期按钮`);
  const classAttr = match[0].match(/class="([^"]*)"/);
  assert.ok(classAttr, `${name} 排期按钮缺少 class`);
  return {tag: match[0], classes: classAttr[1].split(/\s+/).filter(Boolean)};
};

// 统一口径：hw-btn + shrink-0 + min-w-[76px]；首页额外保留 home-topbar-date-control 语义类。
const EXPECTED_COMMON = ['hw-btn', 'shrink-0', 'min-w-[76px]'];
const FORBIDDEN_INLINE = [
  'bg-[#06070a]',
  'border-[#dfc384]/45',
  'gw-type-body-md',
  'shadow-inner',
  'hover:border-[#dfc384]',
  'justify-center',
];

test('全部 10 个页面的排期按钮都使用 hw-btn 机件材质', () => {
  for (const page of PAGES) {
    const {classes} = dateButton(page);
    for (const token of EXPECTED_COMMON) {
      assert.ok(classes.includes(token), `${page} 排期按钮缺少统一 class：${token}`);
    }
  }
});

test('不再保留页面内联的按钮材质覆盖（纯黑底/小圆角/另一套字号）', () => {
  for (const page of PAGES) {
    const {classes} = dateButton(page);
    for (const token of FORBIDDEN_INLINE) {
      assert.ok(!classes.includes(token), `${page} 排期按钮仍带内联覆盖：${token}`);
    }
    assert.ok(!classes.includes('rounded'), `${page} 排期按钮不得再自带圆角，交给 hw-btn`);
  }
});

test('资产库与工作流与其它页面 class 完全一致', () => {
  const reference = dateButton('agents.html').classes.slice().sort();
  for (const page of ['assets.html', 'workflow.html']) {
    assert.deepEqual(dateButton(page).classes.slice().sort(), reference, `${page} 排期按钮口径未对齐`);
  }
});

test('首页保留顶栏排期语义类，其余页面不带该扩展', () => {
  assert.ok(dateButton('index.html').classes.includes('home-topbar-date-control'), '首页须保留语义类');
  for (const page of PAGES.filter((name) => name !== 'index.html')) {
    assert.ok(
      !dateButton(page).classes.includes('home-topbar-date-control'),
      `${page} 不应带首页专用的排期语义类`,
    );
  }
});

test('hw-btn 材质定义存在，按钮依赖的圆角与高度由共享样式提供', () => {
  assert.match(buttonsCss, /:is\(\.hw-btn,/, 'hw-btn 须继续参与共享机件按钮选择器');
  assert.match(buttonsCss, /border-radius:9px/, 'hw-btn 圆角口径应为 9px');
  assert.match(buttonsCss, /--button-height:32px/, 'hw-btn 高度口径应为 32px');
});

test('排期按钮保留可访问性与行为属性', () => {
  for (const page of PAGES) {
    const {tag} = dateButton(page);
    assert.match(tag, /type="button"/, `${page} 排期按钮须为 button`);
    assert.match(tag, /title="打开项目排期日历"/, `${page} 排期按钮缺少 title`);
    assert.match(tag, /aria-label="打开项目排期日历"/, `${page} 排期按钮缺少 aria-label`);
    assert.match(readPage(page), /id="masterDeckDateMonth"/, `${page} 缺少月份占位`);
    assert.match(readPage(page), /id="masterDeckDateDay"/, `${page} 缺少日期占位`);
  }
});
