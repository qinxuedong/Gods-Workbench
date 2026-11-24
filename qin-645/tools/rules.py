"""匹配规则枚举探针（只读）。

对指定页面 + 选择器，枚举真实页面上「命中的全部 CSS 规则」，输出：
  样式表文件名 / 规则序号 / 选择器 / 特异性(a,b,c) / !important / 声明片段
按级联胜出顺序（specificity 升序 + 出现顺序降序）排列，末位即最终生效者。

用法：
  python rules.py <页面名> <css选择器> [属性过滤逗号分隔]
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

PORT = os.environ.get("PROBE_PORT", "2088")
BASE = f"http://127.0.0.1:{PORT}"
WIDTH = int(os.environ.get("PROBE_WIDTH", "1440"))
USERNAME = "qa_local"
PASSWORD = "QaLocal#645pw"

PAGES = {
    "task-center": "/static/embeds/task-center.html?embedded=1&view=tasks",
    "episode-pipeline": "/static/embeds/episode-pipeline.html?embedded=1",
    "governance": "/static/embeds/governance.html?embedded=1",
    "canvas-list": "/static/embeds/canvas-list.html?embedded=1",
    "asset-share": "/static/asset-share.html",
}

LOGIN_JS = """
async ([u, p]) => {
  const r = await fetch('/api/asset-auth/local/login', {
    method: 'POST', credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: u, password: p}),
  });
  return r.status;
}
"""

# 在页面内枚举命中规则。特异性按 CSS Selectors L4 计算：
# - :where() 恒为 0；:is()/:not() 取参数最大值；其余伪类计 1 类。
ENUM_JS = """
([sel, propFilter]) => {
  function specOf(selector) {
    let s = selector;
    // 先把 :where(...) 整体抹成空（特异性 0）
    s = stripFunc(s, ':where');
    // :is() / :not() 求参数最大特异性
    let best = [0, 0, 0];
    for (const fn of [':is', ':not', ':has']) {
      s = replaceFunc(s, fn, (args) => {
        let mx = [0, 0, 0];
        for (const a of splitTop(args)) {
          const sp = specOf(a);
          if (cmp(sp, mx) > 0) mx = sp;
        }
        if (cmp(mx, best) > 0) best = mx;
        return ' ';
      });
    }
    let a = best[0], b = best[1], c = best[2];
    // 去掉属性选择器后统计
    s = s.replace(/\\[[^\\]]*\\]/g, () => { b += 1; return ' '; });
    s = s.replace(/#[A-Za-z0-9_-]+/g, () => { a += 1; return ' '; });
    s = s.replace(/\\.[A-Za-z0-9_-]+/g, () => { b += 1; return ' '; });
    s = s.replace(/:{1,2}[A-Za-z-]+(\\([^)]*\\))?/g, () => { b += 1; return ' '; });
    const types = s.match(/(^|[\\s>+~(,])([A-Za-z][A-Za-z0-9-]*)/g);
    if (types) c += types.length;
    return [a, b, c];
  }
  function splitTop(str) {
    const out = []; let buf = ''; let d = 0;
    for (const ch of str) {
      if (ch === '(' || ch === '[') d += 1;
      else if (ch === ')' || ch === ']') d = Math.max(0, d - 1);
      if (ch === ',' && d === 0) { if (buf.trim()) out.push(buf.trim()); buf = ''; continue; }
      buf += ch;
    }
    if (buf.trim()) out.push(buf.trim());
    return out;
  }
  function stripFunc(str, name) {
    let out = '', i = 0;
    while (i < str.length) {
      if (str.startsWith(name + '(', i)) {
        let d = 0, j = i + name.length;
        for (; j < str.length; j++) {
          if (str[j] === '(') d += 1;
          else if (str[j] === ')') { d -= 1; if (d === 0) break; }
        }
        i = j + 1;
        continue;
      }
      out += str[i++];
    }
    return out;
  }
  function replaceFunc(str, name, fn) {
    let out = '', i = 0;
    while (i < str.length) {
      if (str.startsWith(name + '(', i)) {
        let d = 0, j = i + name.length;
        for (; j < str.length; j++) {
          if (str[j] === '(') d += 1;
          else if (str[j] === ')') { d -= 1; if (d === 0) break; }
        }
        out += fn(str.slice(i + name.length + 1, j));
        i = j + 1;
        continue;
      }
      out += str[i++];
    }
    return out;
  }
  function cmp(x, y) {
    for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
    return 0;
  }

  const props = propFilter.length ? propFilter : null;
  // 支持两种定位：CSS 选择器；或快照结构路径 "html:1>body:1>div:2>select:1"
  let targets = [];
  let trace = [];
  if (sel.indexOf('>') >= 0 && /^(html|body):\\d/.test(sel)) {
    let cur = document.documentElement;
    let steps = sel.split('>');
    for (const part of steps) {
      const [tag, idxRaw] = part.split(':');
      const idx = parseInt(idxRaw, 10);
      if (cur && cur.tagName.toLowerCase() === tag && idx === 1) {
        trace.push(part + ' -> 自身 <' + tag + '>');
        continue;
      }
      if (!cur) { trace.push(part + ' -> 父级缺失'); break; }
      const kids = Array.from(cur.children).filter(c => c.tagName.toLowerCase() === tag);
      trace.push(part + ' -> 同标签子元素 ' + kids.length + ' 个 @ <' + cur.tagName.toLowerCase() + ' class=' + (cur.getAttribute('class') || '') + '>');
      cur = kids[idx - 1] || null;
    }
    if (cur) targets = [cur];
  } else {
    targets = Array.from(document.querySelectorAll(sel)).slice(0, 3);
  }
  const sheets = [];
  let order = 0;
  function walk(rules, href) {
    for (const r of rules) {
      order += 1;
      if (r.cssRules && r.cssRules.length) {
        if (r.media) {
          // media 规则：条件满足才计入
          if (window.matchMedia(r.conditionText).matches) walk(r.cssRules, href + ' @media ' + r.conditionText);
        } else if (r.cssRules) {
          walk(r.cssRules, href);
        }
        continue;
      }
      if (!r.selectorText || !r.style) continue;
      const hitIdx = [];
      targets.forEach((t, i) => {
        try { if (t.matches(r.selectorText)) hitIdx.push(i); } catch (e) { /* 伪元素等 */ }
      });
      if (!hitIdx.length) continue;
      const decl = [];
      for (let k = 0; k < r.style.length; k++) {
        const p = r.style[k];
        if (props && !props.some(x => p === x || p.startsWith(x) || p.includes(x))) continue;
        decl.push(p + ': ' + r.style.getPropertyValue(p) +
                  (r.style.getPropertyPriority(p) ? ' !important' : ''));
      }
      if (!decl.length) continue;
      sheets.push({href, order, sel: r.selectorText,
                   spec: specOf(r.selectorText), decl, hitIdx});
    }
  }
  for (const sh of document.styleSheets) {
    let rules = null;
    try { rules = sh.cssRules; } catch (e) { continue; }
    if (!rules) continue;
    const href = (sh.href || '(inline)').split('/').slice(-2).join('/');
    walk(rules, href);
  }
  // 级联排序：!important 优先；同 important 下 specificity 升序、order 升序（末位胜）
  sheets.sort((x, y) => {
    const ix = x.decl.some(d => d.includes('!important')) ? 1 : 0;
    const iy = y.decl.some(d => d.includes('!important')) ? 1 : 0;
    if (ix !== iy) return ix - iy;
    const c = cmp(x.spec, y.spec);
    if (c !== 0) return c;
    return x.order - y.order;
  });
  return {
    bodyClass: document.body.className,
    theme: document.documentElement.getAttribute('data-theme'),
    count: targets.length,
    trace,
    targets: targets.map((t, i) => {
      const cs = getComputedStyle(t);
      const o = {};
      for (const p of (props || ['min-height', 'border-radius', 'height', 'color', 'font-family', 'background-color'])) {
        o[p] = cs.getPropertyValue(p);
      }
      return {
        idx: i,
        tag: t.tagName.toLowerCase(),
        cls: t.getAttribute('class') || '',
        computed: o,
        rules: sheets.filter(r => r.hitIdx.includes(i)),
      };
    }),
    rules: sheets,
  };
}
"""


def main() -> int:
    page_name = sys.argv[1]
    sel = sys.argv[2]
    prop_filter = [s for s in (sys.argv[3].split(",") if len(sys.argv) > 3 else []) if s]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": WIDTH, "height": 900})
        p0 = ctx.new_page()
        p0.goto(BASE + "/", wait_until="domcontentloaded")
        st = p0.evaluate(LOGIN_JS, [USERNAME, PASSWORD])
        assert st == 200, f"登录失败 status={st}"
        theme = os.environ.get("PROBE_THEME", "")
        if theme:
            p0.goto(BASE + "/static/embeds/canvas-list.html", wait_until="domcontentloaded")
            p0.evaluate(
                "t => { localStorage.setItem('studio_theme', t);"
                " localStorage.setItem('canvas_theme', t);"
                " localStorage.setItem('theme', t); }",
                theme,
            )
        p0.close()

        page = ctx.new_page()
        page.goto(BASE + PAGES[page_name], wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1200)
        data = page.evaluate(ENUM_JS, [sel, prop_filter])
        browser.close()

    print(f"页面 {page_name}  选择器 {sel}  body.class = {data['bodyClass']}  theme={data['theme']}")
    print(f"命中元素数 = {data['count']}")
    for line in data.get('trace') or []:
        print('  路径解析:', line)
    for t in data["targets"]:
        print("=" * 78)
        print(f"[{t['idx']}] <{t['tag']}> class={t['cls'][:60]!r}")
        print(f"    计算值 = {t['computed']}")
        for r in t["rules"]:
            spec = f"({r['spec'][0]},{r['spec'][1]},{r['spec'][2]})"
            print(f"    [{r['order']:5d}] {spec:9s} {r['href']}")
            print(f"           {r['sel'][:140]}")
            print(f"           {'; '.join(r['decl'])[:200]}")
        print(f"    (该元素共 {len(t['rules'])} 条命中规则)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
