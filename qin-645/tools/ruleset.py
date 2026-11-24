"""运行时规则集对拍（只读）。

在基线实例与迁出实例上分别枚举「当前页面上真正命中至少一个元素的全部 CSS 规则」，
按 (归一化选择器, 归一化声明, 媒体条件) 生成签名后做集合差，输出：

  1. 基线有、迁出后没有的规则（真正的漏迁嫌疑）
  2. 迁出后有、基线没有的规则（新增/改写，需要说明）

归一化：去掉公共层追加的 :not(:where(...)) 零特异性护栏、去空白、去引号、转小写。
这样只比对「同一行为」的规则，不比对文本写法的差异。

用法：python ruleset.py [宽度]
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

import os

PORTS = {
    "before": int(os.environ.get("BEFORE_PORT", "2089")),
    "after": int(os.environ.get("AFTER_PORT", "2088")),
}
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

ENUM_JS = """
() => {
  function compress(s) {
    return s.replace(/:not\\(\\s*:where\\([^()]*\\)\\s*\\)/g, '')
            .replace(/["']/g, '')
            .replace(/\\s+/g, '')
            .toLowerCase();
  }
  function stripPseudo(sel) {
    return sel.replace(/::[a-zA-Z-]+(\\([^)]*\\))?/g, '');
  }
  const out = [];
  function matchesAny(sel) {
    const probe = stripPseudo(sel);
    if (!probe.trim()) return false;
    try { return document.querySelector(probe) !== null; } catch (e) { return false; }
  }
  function walk(rules, media) {
    for (const r of rules) {
      // 注意：CSS 嵌套下 CSSStyleRule 也有 cssRules（空表），必须判长度，
      // 否则会把所有普通样式规则当成分组规则跳过。
      const nested = r.cssRules;
      if (r.media) {
        if (!window.matchMedia(r.conditionText).matches) continue;
        walk(nested || [], (media ? media + ' && ' : '') + r.conditionText);
        continue;
      }
      if (r.selectorText && r.style && r.style.length) {
        if (matchesAny(r.selectorText)) {
          const decl = [];
          for (let k = 0; k < r.style.length; k++) {
            const p = r.style[k];
            decl.push(p + ':' + r.style.getPropertyValue(p) + (r.style.getPropertyPriority(p) ? '!' : ''));
          }
          decl.sort();
          out.push({
            sel: compress(r.selectorText),
            rawSel: r.selectorText.replace(/\\s+/g, ' ').slice(0, 160),
            decl: decl.join(';'),
            media: media || '',
            href: (r.parentStyleSheet && r.parentStyleSheet.href ? r.parentStyleSheet.href : '').split('/').pop().split('?')[0],
          });
        }
      }
      if (nested && nested.length) walk(nested, media);
    }
  }
  for (const sh of document.styleSheets) {
    let rules = null;
    try { rules = sh.cssRules; } catch (e) { continue; }
    if (rules) walk(rules, '');
  }
  return out;
}
"""


def collect(width: int) -> dict[str, list[dict]]:
    data: dict[str, list[dict]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for label, port in PORTS.items():
            base = f"http://127.0.0.1:{port}"
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            p0 = ctx.new_page()
            p0.goto(base + "/", wait_until="domcontentloaded")
            if port == 2089:
                p0.evaluate(
                    """async ([u,p]) => { const r = await fetch('/api/asset-auth/local/login',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})}); return r.status; }""",
                    [USERNAME, PASSWORD],
                )
            else:
                assert p0.evaluate(LOGIN_JS, [USERNAME, PASSWORD]) == 200
            for t in ("dark",):
                p0.goto(base + "/static/embeds/canvas-list.html", wait_until="domcontentloaded")
                p0.evaluate(
                    "t => { localStorage.setItem('studio_theme', t);"
                    " localStorage.setItem('canvas_theme', t);"
                    " localStorage.setItem('theme', t); }",
                    t,
                )
            p0.close()
            for name, url in PAGES.items():
                page = ctx.new_page()
                page.goto(base + url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(1000)
                data[f"{label}/{name}"] = page.evaluate(ENUM_JS)
                print(f"  {label:6s} {name:18s} 命中规则 {len(data[f'{label}/{name}'])}")
                page.close()
            ctx.close()
        browser.close()
    return data


def sig(rule: dict) -> str:
    return rule["sel"] + "|" + rule["decl"] + "|" + rule["media"]


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
    data = collect(width)
    report = {}
    print()
    for name in PAGES:
        before = data[f"before/{name}"]
        after = data[f"after/{name}"]
        bs = {sig(r): r for r in before}
        as_ = {sig(r): r for r in after}
        missing = [bs[k] for k in bs if k not in as_]
        added = [as_[k] for k in as_ if k not in bs]
        report[name] = {
            "before_rules": len(before),
            "after_rules": len(after),
            "missing": missing,
            "added": added,
        }
        print("=" * 78)
        print(f"{name}: 基线 {len(before)} 条 / 迁出后 {len(after)} 条  "
              f"缺失 {len(missing)} 条  新增 {len(added)} 条")
        for r in missing:
            print(f"  [缺失] {r['href']} {('<'+r['media']+'> ') if r['media'] else ''}{r['rawSel']}")
            print(f"         {r['decl'][:200]}")
    out = __import__("pathlib").Path(__file__).resolve().parent / f"ruleset-{width}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
