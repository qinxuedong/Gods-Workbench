"""变量层漂移审计（只读）。

对比基线实例与迁出实例上，body 计算后取到的自定义属性值。
迁移把 signal-flow 的 :root 变量块下沉到了 body.gw-signal，
自定义属性走「就近祖先」继承，因此 body 级声明会压过页面在 html 级
（.theme-dark / [data-theme] / :root）的同名定义。此脚本量化该影响范围。

用法：python vars.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PORTS = {"before": 2089, "after": 2088}
USERNAME, PASSWORD = "qa_local", "QaLocal#645pw"

PAGES = {
    "task-center": "/static/embeds/task-center.html?embedded=1&view=tasks",
    "episode-pipeline": "/static/embeds/episode-pipeline.html?embedded=1",
    "governance": "/static/embeds/governance.html?embedded=1",
    "canvas-list": "/static/embeds/canvas-list.html?embedded=1",
    "asset-share": "/static/asset-share.html",
}

NAMES = [
    "--sf-bg", "--sf-surface", "--sf-content", "--sf-raised", "--sf-line", "--sf-line-strong",
    "--sf-text", "--sf-muted", "--sf-faint", "--sf-cyan", "--sf-mint", "--sf-amber",
    "--sf-coral", "--sf-indigo", "--sf-on-accent",
    "--gw-bg", "--gw-surface", "--gw-line", "--gw-text", "--gw-muted", "--gw-faint",
    "--page", "--bg", "--stage-bg", "--sidebar-bg", "--panel", "--panel-bg", "--card",
    "--soft", "--soft-2", "--line", "--line-2", "--text", "--muted", "--faint", "--strong",
    "--strong-text", "--accent", "--accent-soft", "--accent-line", "--brand", "--primary",
    "--success", "--danger", "--shadow", "--shadow-strong", "--border", "--stage-border",
    "--nav-hover-bg", "--scrollbar", "--scrollbar-hover",
]

LOGIN = """
async ([u,p]) => { const r = await fetch('/api/asset-auth/local/login',
 {method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({username:u,password:p})}); return r.status; }
"""

READ = """
(names) => {
  const cs = getComputedStyle(document.body);
  const hs = getComputedStyle(document.documentElement);
  const out = {};
  for (const n of names) out[n] = [cs.getPropertyValue(n).trim(), hs.getPropertyValue(n).trim()];
  return out;
}
"""


def main() -> int:
    data: dict[str, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for label, port in PORTS.items():
            base = f"http://127.0.0.1:{port}"
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            p0 = ctx.new_page()
            p0.goto(base + "/", wait_until="domcontentloaded")
            if port == 2089:
                p0.evaluate(LOGIN, [USERNAME, PASSWORD])
            else:
                assert p0.evaluate(LOGIN, [USERNAME, PASSWORD]) == 200
            p0.goto(base + "/static/embeds/canvas-list.html", wait_until="domcontentloaded")
            p0.evaluate("t => { localStorage.setItem('studio_theme', t);"
                        " localStorage.setItem('canvas_theme', t);"
                        " localStorage.setItem('theme', t); }", "dark")
            p0.close()
            for theme in ("dark", "light"):
                if theme == "light":
                    seed = ctx.new_page()
                    seed.goto(base + "/static/embeds/canvas-list.html", wait_until="domcontentloaded")
                    seed.evaluate("t => { localStorage.setItem('studio_theme', t);"
                                  " localStorage.setItem('canvas_theme', t);"
                                  " localStorage.setItem('theme', t); }", "light")
                    seed.close()
                for name, url in PAGES.items():
                    page = ctx.new_page()
                    page.goto(base + url, wait_until="networkidle", timeout=45000)
                    page.wait_for_timeout(600)
                    data[f"{label}/{theme}/{name}"] = page.evaluate(READ, NAMES)
                    page.close()
            ctx.close()
        browser.close()

    total = 0
    report = {}
    for theme in ("dark", "light"):
        for name in PAGES:
            b = data[f"before/{theme}/{name}"]
            a = data[f"after/{theme}/{name}"]
            diffs = {n: {"before": b[n], "after": a[n]} for n in NAMES if b[n] != a[n]}
            total += len(diffs)
            report[f"{theme}/{name}"] = diffs
            if diffs:
                print(f"== {theme}/{name}: {len(diffs)} 个变量不一致")
                for n, v in diffs.items():
                    print(f"   {n:20s} before(b={v['before'][0] or '-'}, h={v['before'][1] or '-'}) "
                          f"after(b={v['after'][0] or '-'}, h={v['after'][1] or '-'})")
    print(f"\n合计不一致变量 {total} 处（40 个变量名 × 5 页 × 2 主题）")
    out = Path(__file__).resolve().parent / "vars.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
