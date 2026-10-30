// QIN-491 (GitHub #496): 顶栏上层机架中央状态位不得压在播控/时钟/排期组下方
// 回归门禁：右端组必须留在自身行内（行内右对齐），不得再用绝对定位脱离文档流；
// 否则中央状态位会滑到右端组底下，在同一位置叠出一层多余内容。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('../../web/css/base/hardware-design-system.css', import.meta.url), 'utf8');
const readPage = (name) => readFileSync(new URL(`../../web/pages/${name}`, import.meta.url), 'utf8');

/** 取出选择器命中的全部规则块文本（同一选择器可能有多段，逐段校验）。 */
const blocksOf = (source, selector) => {
  const blocks = [];
  let from = 0;
  for (;;) {
    const start = source.indexOf(selector, from);
    if (start < 0) break;
    const open = source.indexOf('{', start);
    const nextSelector = source.indexOf('}', start);
    // 只接受紧跟该选择器（中间不含 }）的规则块，避免跨规则误配。
    if (open >= 0 && (nextSelector < 0 || open < nextSelector)) {
      let depth = 0;
      for (let i = open; i < source.length; i += 1) {
        if (source[i] === '{') depth += 1;
        else if (source[i] === '}') {
          depth -= 1;
          if (depth === 0) {
            blocks.push(source.slice(open + 1, i));
            from = i;
            break;
          }
        }
      }
    }
    from = start + selector.length;
  }
  assert.ok(blocks.length > 0, `缺少选择器 ${selector}`);
  return blocks;
};

const upperRightBlocks = blocksOf(css, '.topbar-master-deck .master-deck-upper-right');
const upperBlocks = blocksOf(css, '.topbar-master-deck .master-deck-upper');

test('右端组留在自身行内，不再绝对定位脱离文档流', () => {
  const merged = upperRightBlocks.join('\n');
  assert.match(merged, /position:\s*static/, '右端组必须为 static，避免与中央状态位同位置叠层');
  assert.match(merged, /margin-left:\s*auto/, '右端组须以行内自动边距贴右');
  assert.match(merged, /flex:\s*0 0 auto/, '右端组不得被压缩，必达控件须完整可见');
  for (const block of upperRightBlocks) {
    assert.doesNotMatch(block, /position:\s*absolute/, '右端组任何规则都不得再使用绝对定位');
    assert.doesNotMatch(block, /right:\s*8px/, '右端组任何规则都不得再贴绝对右偏移');
  }
});

test('上层机架保持相对定位且允许收缩，中央状态位按整行排布', () => {
  const merged = upperBlocks.join('\n');
  assert.match(merged, /position:\s*relative/, '上层机架须为相对定位容器');
  assert.match(merged, /min-width:\s*0/, '上层机架须允许收缩，不得撑出固定下限');
});

test('三页顶栏保留中央状态位与右端播控/时钟/排期组', () => {
  for (const page of ['index.html', 'agents.html', 'settings.html']) {
    const html = readPage(page);
    assert.match(html, /master-deck-upper-right/, `${page} 须保留右端组语义类`);
    assert.match(html, /id="masterDeckClock"/, `${page} 须保留顶栏时钟`);
    assert.match(html, /id="masterDeckDate"/, `${page} 须保留顶栏排期入口`);
    assert.match(html, /class="[^"]*hw-round-btn/, `${page} 须保留顶栏播控按键`);
  }
});

test('项目中心顶栏不引用该语义类，不受本次改动影响', () => {
  const html = readPage('projects.html');
  assert.doesNotMatch(html, /master-deck-upper-right/, '项目中心不应引用该语义类');
  assert.match(html, /topbar-master-deck/, '项目中心仍使用固定顶栏');
});

test('窄屏收起规则保持生效（QIN-480 口径）', () => {
  assert.match(
    css,
    /@media\s*\(max-width:\s*1450px\)[\s\S]*?\.master-deck-upper-right\s*>\s*div:nth-of-type\(1\)[\s\S]*?display:\s*none/,
    '≤1450px 收起装饰组的既有规则不得被移除',
  );
});
