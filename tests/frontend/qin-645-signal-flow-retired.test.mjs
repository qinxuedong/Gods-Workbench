/**
 * QIN-645：内嵌/分享页整体迁出并下线 signal-flow.css 的防回归门禁。
 *
 * 覆盖五类不可回退的契约：
 *   1. 旧样式文件已物理下线，且**全库**（HTML / CSS / JS）不再有任何引用；
 *   2. 五个目标页接入硬件设计系统四件套，并携带 .gw-legacy-signal 文档标记；
 *   3. 公共层每条选择器都必须被该标记门控 —— 范围外页面结构上不可能命中；
 *   4. 旧名别名变量只能落在 :root 级或主题块的 !important 声明里，
 *      不得在 body 级以普通声明出现（否则会盖掉页面在 <html> 上的 .theme-dark）；
 *   5. 公共层完整承接明暗两套语义变量与控件基础层。
 */
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = relativePath => readFileSync(path.join(root, relativePath), 'utf8');
const exists = relativePath => existsSync(path.join(root, relativePath));

/** 递归列出 web/ 下的全部文件（相对仓库根，统一正斜杠）。 */
function walkWeb(dir = 'web') {
  const out = [];
  for (const entry of readdirSync(path.join(root, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...walkWeb(rel));
    else out.push(rel);
  }
  return out;
}

const WEB_FILES = walkWeb();
const HTML_FILES = WEB_FILES.filter(file => file.endsWith('.html'));
const TEXT_ASSETS = WEB_FILES.filter(file => /\.(html|css|js|mjs|json)$/.test(file));

const TARGETS = {
  'web/embeds/task-center.html': 'gw-page--task-center',
  'web/embeds/episode-pipeline.html': 'gw-page--episode-pipeline',
  'web/embeds/governance.html': 'gw-page--governance',
  'web/embeds/canvas-list.html': 'gw-page--canvas',
  'web/asset-share.html': 'gw-page--asset-share',
};

// 范围外但同为 body.gw-signal 的页面：从未加载过 signal-flow.css，
// 它的金色主题由 asset-hardware-theme.css 把 --sf-* 映射到 --gold-primary 提供。
const OUT_OF_SCOPE = 'web/embeds/asset-manager.html';

const MARKER = 'gw-legacy-signal';
const LAYER_BANNER = 'QIN-645 内嵌/分享页公共层';
const COMMON = [
  'css/base/hardware-tokens.css',
  'css/base/hardware-design-system.css',
  'css/base/hardware-buttons.css',
  'css/base/workspace-pages.css',
];

/** 取 <body> 的 class 列表。 */
function bodyClasses(html) {
  const match = html.match(/<body[^>]*\bclass="([^"]*)"/);
  return match ? match[1].split(/\s+/).filter(Boolean) : [];
}

/** 取公共层文本：从承载标题注释的 /* 起算，避免切在注释中间导致剥离失败。 */
function layerSource() {
  const css = read('web/css/base/hardware-design-system.css');
  const at = css.indexOf(LAYER_BANNER);
  assert.ok(at >= 0, `公共层缺失：${LAYER_BANNER}`);
  const commentStart = css.lastIndexOf('/*', at);
  return css.slice(commentStart >= 0 ? commentStart : at);
}

/**
 * 结构解析一条复合选择器里「根元素（body）」那一段的类/属性约束，
 * 用于判断给定 body 的 class 列表是否可能命中。
 * 只支持本层实际用到的语法：.cls、:where(.cls)、:is(.a, .b)、:not(.cls)、
 * :not(:where(.cls))、[attr]、[attr="v"]、伪元素。
 */
function parseRootCompound(selector) {
  // 按顶层组合符切分，取出**主体**复合选择器：优先 body 段（html body.foo 的主体是 body.foo），
  // 否则取最后一段；html/:root 前缀在本层只作标签级限定，不携带类约束。
  const compounds = [];
  let buf = '';
  let depth = 0;
  for (const ch of selector) {
    if (ch === '(' || ch === '[') depth += 1;
    else if (ch === ')' || ch === ']') depth = Math.max(0, depth - 1);
    if (depth === 0 && (ch === ' ' || ch === '>' || ch === '+' || ch === '~')) {
      if (buf.trim()) compounds.push(buf.trim());
      buf = '';
      continue;
    }
    buf += ch;
  }
  if (buf.trim()) compounds.push(buf.trim());
  const subject = compounds.find(part => /^body\b/.test(part)) || compounds[compounds.length - 1] || '';
  const sink = { required: new Set(), excluded: new Set(), alternatives: [], attrs: [] };

  // 只解析本层实际用到的语法：.cls、:where()、:is()、:not()、:has()、[attr]。
  // :has() 约束的是后代/子元素，不约束根元素自身，直接跳过。
  const scan = (text, out) => {
    let i = 0;
    while (i < text.length) {
      const ch = text[i];
      if (ch === '.') {
        const name = /^[\w-]+/.exec(text.slice(i + 1))?.[0];
        if (name) out.required.add(name);
        i += 1 + (name ? name.length : 0);
        continue;
      }
      if (ch === '[') {
        const end = text.indexOf(']', i);
        if (end > i) out.attrs.push(text.slice(i + 1, end));
        i = end > i ? end + 1 : i + 1;
        continue;
      }
      if (ch === ':') {
        const fn = /^::?([\w-]+)\(/.exec(text.slice(i));
        if (!fn) { i += 1; continue; }
        const open = i + fn[0].length - 1;
        let level = 0;
        let close = open;
        for (let j = open; j < text.length; j += 1) {
          if (text[j] === '(') level += 1;
          else if (text[j] === ')') {
            level -= 1;
            if (level === 0) { close = j; break; }
          }
        }
        const inner = text.slice(open + 1, close);
        const name = fn[1];
        if (name === 'not') {
          const negated = { required: new Set(), excluded: new Set(), alternatives: [], attrs: [] };
          scan(inner, negated);
          for (const cls of negated.required) out.excluded.add(cls);
        } else if (name === 'is') {
          const branchClasses = splitTopLevel(inner).map(branch => {
            const branchSink = { required: new Set(), excluded: new Set(), alternatives: [], attrs: [] };
            scan(branch, branchSink);
            return [...branchSink.required];
          });
          if (branchClasses.some(list => list.length)) out.alternatives.push(branchClasses);
        } else if (name === 'has') {
          // 本层的 :has() 只用于「文档级门控」——html:has(> body.gw-signal:where(标记))，
          // 约束的正是 body 自身，故按其内部类集合当作必需项处理。
          scan(inner, out);
        } else {
          scan(inner, out);
        }
        i = close + 1;
        continue;
      }
      i += 1;
    }
  };

  scan(subject, sink);
  return sink;
}

/** 给定 body class 列表，判断某条选择器的根段是否可能命中。 */
function rootMayMatch(selector, classes, attrs = {}) {
  const { required, alternatives, excluded, attrs: attrReqs } = parseRootCompound(selector);
  for (const cls of required) if (!classes.includes(cls)) return false;
  for (const cls of excluded) if (classes.includes(cls)) return false;
  for (const branchClasses of alternatives) {
    if (branchClasses.length && !branchClasses.some(cls => classes.includes(cls))) return false;
  }
  for (const req of attrReqs) {
    const [name, value] = req.split('=').map(part => part.trim().replace(/^["']|["']$/g, ''));
    if (!(name in attrs)) return false;
    if (value && attrs[name] !== value) return false;
  }
  return true;
}

/** 抽出公共层里的所有规则选择器（顶层逗号切分，跳过 @media 包裹）。 */
function layerSelectors(source) {
  const stripped = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const selectors = [];
  let buffer = '';
  let depth = 0;
  for (let i = 0; i < stripped.length; i += 1) {
    const ch = stripped[i];
    if (ch === '{') {
      const prelude = buffer.trim().replace(/\s+/g, ' ');
      // @media 等 at-rule 的前导不算选择器，但其内部的规则同样要纳入门控检查。
      if (prelude && !prelude.startsWith('@')) {
        selectors.push(...splitTopLevel(prelude));
      }
      buffer = '';
      depth += 1;
      continue;
    }
    if (ch === '}') {
      depth = Math.max(0, depth - 1);
      buffer = '';
      continue;
    }
    buffer += ch;
  }
  return selectors;
}

/** 按顶层逗号切分选择器组（:is()/:where()/:not() 内的逗号不算）。 */
function splitTopLevel(prelude) {
  const out = [];
  let buf = '';
  let depth = 0;
  for (const ch of prelude) {
    if (ch === '(' || ch === '[') depth += 1;
    else if (ch === ')' || ch === ']') depth = Math.max(0, depth - 1);
    if (ch === ',' && depth === 0) {
      if (buf.trim()) out.push(buf.trim());
      buf = '';
      continue;
    }
    buf += ch;
  }
  if (buf.trim()) out.push(buf.trim());
  return out;
}

test('QIN-645：signal-flow.css 已物理下线', () => {
  assert.equal(exists('web/css/base/signal-flow.css'), false, '旧样式文件必须已删除');
});

test('QIN-645：全库 HTML/CSS/JS 不再引用 signal-flow', () => {
  // 注释里提到旧文件名属文档说明，先剥注释再判，避免把说明当成引用。
  const stripComments = text =>
    text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/<!--[\s\S]*?-->/g, '');
  const offenders = [];
  for (const file of TEXT_ASSETS) {
    if (stripComments(read(file)).includes('signal-flow')) offenders.push(file);
  }
  assert.deepEqual(offenders, [], `以下文件仍引用 signal-flow：${offenders.join(', ')}`);
});

test('QIN-645：五个目标页接入四件套并携带文档标记', () => {
  for (const [page, bodyClass] of Object.entries(TARGETS)) {
    const html = read(page);
    const classes = bodyClasses(html);
    assert.ok(classes.includes(bodyClass), `${page} 的 body 归属页类丢失`);
    assert.ok(classes.includes('gw-signal'), `${page} 需保留 gw-signal`);
    assert.ok(
      classes.includes(MARKER),
      `${page} 必须携带 .${MARKER}，否则公共层整体失效`,
    );
    for (const css of COMMON) {
      assert.match(
        html,
        new RegExp(`<link[^>]+${css.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`),
        `${page} 缺少 ${css}`,
      );
    }
  }
});

test('QIN-645：文档标记只出现在五个目标页，范围外页面不得携带', () => {
  const carriers = HTML_FILES.filter(file => bodyClasses(read(file)).includes(MARKER));
  assert.deepEqual(
    [...carriers].sort(),
    Object.keys(TARGETS).sort(),
    '携带 .gw-legacy-signal 的页面集合必须恰好等于五个目标页',
  );
  assert.ok(
    !bodyClasses(read(OUT_OF_SCOPE)).includes(MARKER),
    `${OUT_OF_SCOPE} 从未加载过 signal-flow.css，不得携带公共层标记`,
  );
});

test('QIN-645：canvas-list 不再引用已删除的 canvasLegacySignal', () => {
  const html = read('web/embeds/canvas-list.html');
  assert.doesNotMatch(html, /canvasLegacySignal/, '悬空引用会在 overview 布局下抛空指针');
  // 同段的 legacy theme 链接属本票范围外，须保留并带存在性判空。
  assert.match(html, /id="canvasLegacyTheme"/, 'theme.css 链接应保持不变');
  assert.match(
    html,
    /var legacyTheme = document\.getElementById\('canvasLegacyTheme'\);\s*if \(legacyTheme\) legacyTheme\.disabled = true;/,
    'legacy theme 关闭逻辑需判空',
  );
});

test('QIN-645：公共层每条选择器都被 .gw-legacy-signal 门控', () => {
  const selectors = layerSelectors(layerSource());
  assert.ok(selectors.length > 40, `公共层规则数量异常：${selectors.length}`);
  for (const selector of selectors) {
    assert.match(
      selector,
      new RegExp(`:where\\(\\.${MARKER}\\)`),
      `公共层出现未被标记门控的选择器：${selector.slice(0, 100)}`,
    );
  }
  // 排除护栏：所有 body 级规则都带 :not(:where(.gw-shell))。
  const bodyRules = selectors.filter(selector => selector.startsWith('body.gw-signal'));
  assert.ok(bodyRules.length > 0, '缺少 body.gw-signal 规则');
  for (const selector of bodyRules) {
    assert.match(
      selector,
      /:not\(:where\(\.gw-shell\)\)/,
      `body 规则缺少 gw-shell 排除护栏：${selector.slice(0, 100)}`,
    );
  }
});

test('QIN-645：范围外页面结构上不可能命中公共层（污染反证）', () => {
  const selectors = layerSelectors(layerSource());
  const cases = [
    {
      file: OUT_OF_SCOPE,
      attrs: {},
      note: '资产页金色主题由 --gold-primary 提供',
    },
  ];
  // 主工作台（gw-shell）：仍须被排除护栏挡住。
  for (const file of ['web/pages/assets.html', 'web/pages/index.html', 'web/pages/settings.html']) {
    if (!exists(file)) continue;
    cases.push({ file, attrs: {}, note: '主工作台 gw-shell' });
  }

  for (const { file, attrs, note } of cases) {
    const classes = bodyClasses(read(file));
    assert.ok(classes.length > 0, `${file} 解析不到 body class`);
    for (const selector of selectors) {
      assert.equal(
        rootMayMatch(selector, classes, attrs),
        false,
        `${file}（${note}）不应命中公共层规则：${selector.slice(0, 120)}`,
      );
    }
  }

  // 解析器自检：同一批选择器在目标页 body 上必须能命中，否则上面的「不命中」毫无意义。
  const targetClasses = bodyClasses(read('web/embeds/canvas-list.html'));
  const matched = selectors.filter(selector => rootMayMatch(selector, targetClasses, {}));
  assert.ok(matched.length >= 5, `解析器自检失败：目标页仅命中 ${matched.length} 条规则`);
});

test('QIN-645：旧名别名不得在 body 级以普通声明出现', () => {
  const source = layerSource().replace(/\/\*[\s\S]*?\*\//g, '');
  // 基础变量块必须在 :root 级（:root:where(:has(...))），特异性等同迁移前的 :root。
  assert.match(
    source,
    /:root:where\(:has\(> body\.gw-signal:where\(\.gw-legacy-signal\)\)\)\s*\{/,
    '语义变量与旧名别名必须落在 :root 级，否则会盖掉页面在 <html> 上的 .theme-dark',
  );
  // 逐块扫描：body.gw-signal 规则块内的旧名别名必须带 !important（只在明色块出现）。
  const legacyNames = [
    '--strong', '--strong-text', '--accent', '--accent-soft', '--accent-line',
    '--page', '--panel', '--panel-solid', '--card', '--card-solid',
    '--text', '--muted', '--faint', '--soft', '--soft-2', '--line', '--line-2',
    '--bg', '--brand', '--primary', '--border',
  ];
  const blockRe = /([^{}]+)\{([^{}]*)\}/g;
  let match;
  while ((match = blockRe.exec(source)) !== null) {
    const selector = match[1].trim().replace(/\s+/g, ' ');
    const body = match[2];
    if (!selector.startsWith('body.gw-signal')) continue;
    for (const name of legacyNames) {
      const decl = new RegExp(`(^|;|\\s)${name}\\s*:([^;]*)`, 'g');
      let declMatch;
      while ((declMatch = decl.exec(body)) !== null) {
        const value = declMatch[2];
        assert.match(
          value,
          /!important/,
          `${selector} 在 body 级用普通声明覆盖了页面变量 ${name}，会破坏页面主题`,
        );
      }
    }
  }
});

test('QIN-645：窄屏控件高度白名单只含目标页页类', () => {
  const source = layerSource();
  // 注释里也提到了该断点，按行首锚定真正的 at-rule。
  const at = source.search(/^@media \(max-width: 900px\)/m);
  assert.ok(at > 0, '缺少 900px 断点');
  const mediaBlock = source.slice(at);
  const block = mediaBlock.slice(0, mediaBlock.indexOf('\n}') + 2);
  const pageClasses = [...block.matchAll(/\.gw-page--[\w-]+/g)].map(match => match[0]);
  assert.ok(pageClasses.length > 0, '900px 断点缺少页类白名单');
  const allowed = new Set(Object.values(TARGETS).map(name => `.${name}`));
  for (const pageClass of pageClasses) {
    assert.ok(
      allowed.has(pageClass),
      `900px 白名单出现范围外页类 ${pageClass}：本层不会命中它，留着就是给范围外页面埋雷`,
    );
  }
  assert.ok(
    !pageClasses.includes('.gw-page--asset-manager'),
    '资产页不在本票范围，不得进入窄屏高度白名单',
  );
});

test('QIN-645：组件圆角层的 html 前缀不得被去掉或叠加', () => {
  const source = layerSource();
  const at = source.indexOf('组件圆角收紧层');
  assert.ok(at > 0, '缺少组件圆角收紧层');
  const rule = source.slice(at);
  const selector = rule
    .slice(rule.indexOf('*/') + 2)
    .split('{')[0]
    .replace(/\s+/g, ' ')
    .trim();
  assert.match(
    selector,
    /^html body\.gw-signal:where\(\.gw-legacy-signal\):not\(\.gw-page--home\):not\(:where\(\.gw-shell\)\) /,
    '6px 圆角层的前缀形态被改动：它会重新输给后加载的 8px 面板圆角',
  );
  assert.match(rule, /border-radius: var\(--sf-radius-control\) !important/, '6px 取值丢失');
});

test('QIN-645：公共层承接暗色与明色两套语义变量', () => {
  const block = layerSource();
  // 暗色实值块：非首页路由在暗色下的真实取值。
  assert.match(
    block,
    /body\.gw-signal[^{]*:not\(\.gw-page--home\):not\(\[data-theme="light"\]\):not\(\.theme-light\)[^{]*\{[^}]*--sf-content:\s*#111b23\s*!important/s,
    '缺少暗色语义变量实值块',
  );
  assert.match(block, /--sf-faint:\s*#71808a\s*!important/, '缺少暗色 faint 取值');
  // 明色块：整套变量切浅色，且带 color-scheme。
  assert.match(block, /html body\.gw-signal[^{]*\[data-theme="light"\]/, '缺少明色变量块');
  assert.match(block, /--sf-bg:\s*#f1f0ec\s*!important/, '缺少明色背景取值');
  assert.match(block, /color-scheme:\s*light\s*!important/, '明色缺少 color-scheme');
});

test('QIN-645：公共层承接控件基础层', () => {
  const block = layerSource();
  assert.match(block, /--sf-radius-control:\s*6px/, '缺少控件圆角令牌');
  assert.match(
    block,
    /:is\(select, textarea, input:not\(\[type="color"\]\)[^{]*\{[^}]*min-height:\s*40px/s,
    '缺少表单控件最小高度',
  );
  assert.match(block, /min-height:\s*36px/, '缺少非硬件按钮控件最小高度');
  assert.match(block, /:focus-visible/, '缺少焦点可见样式');
  assert.match(block, /--sf-font-mono/, '缺少等宽字体令牌');
});
