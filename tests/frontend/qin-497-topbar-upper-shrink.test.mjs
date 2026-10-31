// QIN-497 (GitHub #507)：素材页 / 工作流页上层机架须参与共享的收缩与省略规则。
// 回归门禁：这两页长期缺 `.master-deck-upper` 语义类，QIN-491 的行内可收缩修复
// 对它们无效，中央状态位保持 `shrink-0` 且不省略，项目名稍长就把播控 / 时钟 / 排期
// 顶出 `header{overflow:hidden}` 被裁切（真实浏览器量测见 tests/e2e/
// test_qin_497_topbar_schedule_clip.py）。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('../../web/css/base/hardware-design-system.css', import.meta.url), 'utf8');
const readPage = (name) => readFileSync(new URL(`../../web/pages/${name}`, import.meta.url), 'utf8');

const LEGACY_PAGES = ['assets.html', 'workflow.html'];

/** 取页面里上层机架行（带语义类的 header 直属行）的 class 列表与整页源码。 */
const upperDeckRow = (name) => {
  const html = readPage(name);
  const rows = [...html.matchAll(/<div class="([^"]*\bmaster-deck-upper\b[^"]*)">/g)];
  assert.equal(rows.length, 1, `${name} 须恰有一处 master-deck-upper 语义类`);
  const dateIndex = html.indexOf('id="masterDeckDate"');
  assert.ok(dateIndex > rows[0].index, `${name} 语义类须标在含排期入口的上层机架行`);
  return {
    classes: rows[0][1].split(/\s+/).filter(Boolean),
    tag: rows[0][0],
    html,
  };
};

test('素材页与工作流页上层机架带 master-deck-upper 语义类', () => {
  for (const page of LEGACY_PAGES) {
    const {classes} = upperDeckRow(page);
    assert.ok(
      classes.includes('master-deck-upper'),
      `${page} 上层机架缺少 master-deck-upper，共享收缩规则不会生效`,
    );
  }
});

test('上层机架保留原有布局类，语义类只做加法', () => {
  for (const page of LEGACY_PAGES) {
    const {classes} = upperDeckRow(page);
    for (const token of ['flex', 'items-center', 'justify-between', 'relative', 'z-30']) {
      assert.ok(classes.includes(token), `${page} 上层机架丢了布局类：${token}`);
    }
  }
});

test('语义类行只用 class 声明行为，不以内联样式对抗共享规则', () => {
  for (const page of LEGACY_PAGES) {
    const {tag} = upperDeckRow(page);
    for (const property of ['min-width', 'overflow', 'flex-shrink', 'flex-wrap']) {
      assert.ok(!tag.includes(property), `${page} 上层机架不得内联 ${property}`);
    }
  }
});

test('共享规则仍以 header 直属子元素为前提，行不得改挂到内层容器', () => {
  assert.match(
    css,
    /\.topbar-master-deck\s*>\s*\.master-deck-upper\.flex\s*\{[^}]*min-width:\s*0/,
    '共享规则要求语义类行是 header 直属子元素且可收缩',
  );
  for (const page of LEGACY_PAGES) {
    const {html} = upperDeckRow(page);
    const header = html.slice(html.indexOf('<header'), html.indexOf('</header>'));
    const nested = (header.match(/<div class="[^"]*master-deck-upper/g) || []).length;
    assert.equal(nested, 1, `${page} 语义类只允许出现在 header 直属行上`);
  }
});

test('中央状态位可收缩并省略的规则仍然存在（本单生效前提）', () => {
  assert.match(
    css,
    /\.topbar-master-deck \.master-deck-upper > div:nth-child\(2\)\s*\{[^}]*flex:\s*0 1 auto/,
    '中央状态位须允许收缩，否则长文案仍会把右端组顶出顶栏',
  );
  assert.match(
    css,
    /\.topbar-master-deck \.master-deck-upper > div:nth-child\(2\) span\s*\{[^}]*text-overflow:\s*ellipsis/,
    '中央状态位文案须以省略号收口',
  );
});

test('QIN-494 的统一排期按钮口径不被回退', () => {
  for (const page of LEGACY_PAGES) {
    const {html} = upperDeckRow(page);
    const button = html.match(/<button[^>]*id="masterDeckDate"[^>]*>/);
    assert.ok(button, `${page} 缺少顶栏排期按钮`);
    for (const token of ['hw-btn', 'shrink-0', 'min-w-[76px]']) {
      assert.ok(button[0].includes(token), `${page} 排期按钮丢了统一 class：${token}`);
    }
  }
});

test('窄屏收起装饰表的既有断点保持生效', () => {
  assert.match(
    css,
    /@media\s*\(max-width:\s*1450px\)[\s\S]*?\.topbar-master-deck > div:nth-child\(4\) > div:last-child > div:first-child[\s\S]*?display:\s*none/,
    '≤1450px 收起旧版页面 RAM 装饰表的规则不得被移除',
  );
});
