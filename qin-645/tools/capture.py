"""QIN-645 视觉取证采集器。

对 5 个内嵌/分享页 × 4 宽度 × dark/light = 40 组，采集：
  - 截图（PNG）
  - 全量元素 computedStyle 快照（供迁出前后逐元素逐属性 diff）
  - 文本溢出 / 按钮塌陷 / 页面错误等硬判据读数

用法：
  python capture.py <输出目录标签>
  python capture.py before   -> 写入 <qa>/before/
  python capture.py after    -> 写入 <qa>/after/

脚本只读页面，不修改仓库。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import os

BASE = os.environ.get("CAPTURE_BASE", "http://127.0.0.1:2088")
USERNAME = "qa_local"
PASSWORD = "QaLocal#645pw"

QA_ROOT = Path(__file__).resolve().parent

# interact：进入页面后强制一组交互态，用于覆盖「默认态看不出来、交互后才显色」的变量类回归。
# 画布页的 --strong/--strong-text/--accent 只在选中行、选中卡片与状态条显示时参与渲染。
# 空实例没有项目行与画布卡片，而 --strong/--strong-text/--accent 恰恰只在
# 「选中项目行 / 选中卡片 / 状态条显示」时参与渲染。这里按 canvas-list.js
# renderProjects() 的原始类名与嵌套结构，在真实页面内补一个同构节点，
# 让真实 CSS 规则在这些元素上求值；两侧注入完全一致，synthetic 标记随证据留存。
INTERACT_CANVAS = """
() => {
  const out = {};
  const list = document.getElementById('projectList');
  let row = document.querySelector('.ws-project-row');
  if (!row && list) {
    row = document.createElement('div');
    row.className = 'ws-project-row';
    row.innerHTML = '<span class="ws-project-icon"></span>'
      + '<span class="ws-project-name">QIN-645 交互态探针</span>'
      + '<span class="ws-project-count">0</span>';
    list.appendChild(row);
    out.syntheticRow = true;
  }
  if (row) { row.classList.add('active'); out.row = true; }

  const host = document.querySelector('#workspace > main') || document.getElementById('workspace');
  let card = document.querySelector('.ws-card');
  if (!card && host) {
    card = document.createElement('div');
    card.className = 'ws-card';
    card.innerHTML = '<div class="ws-card-head"><span class="ws-card-title">QIN-645 交互态探针</span></div>';
    host.appendChild(card);
    out.syntheticCard = true;
  }
  if (card) { card.classList.add('selected'); out.card = true; }

  const status = document.querySelector('.ws-status');
  if (status) { status.classList.add('show'); out.status = true; }
  const trash = document.querySelector('.ws-trash-note');
  if (trash) { out.trash = true; }
  return out;
}
"""

PAGES = [
    {"name": "task-center", "url": "/static/embeds/task-center.html?embedded=1&view=tasks"},
    {"name": "episode-pipeline", "url": "/static/embeds/episode-pipeline.html?embedded=1"},
    {"name": "governance", "url": "/static/embeds/governance.html?embedded=1"},
    {"name": "canvas-list", "url": "/static/embeds/canvas-list.html?embedded=1"},
    {"name": "canvas-interactive", "url": "/static/embeds/canvas-list.html?embedded=1",
     "interact": INTERACT_CANVAS},
    # 总览布局：该分支会把 body.className 整串重写（连 gw-signal 一起丢掉），
    # 是「文档级变量是否随 body 类消失」的关键取证面。
    {"name": "canvas-overview", "url": "/static/embeds/canvas-list.html?embedded=1&layout=overview"},
    {"name": "asset-share", "url": "/static/asset-share.html"},
    # 范围外页面：从未加载过 signal-flow.css，用于证明公共层未把它的金色主题染成青色。
    {"name": "asset-manager", "url": "/static/embeds/asset-manager.html?embedded=1"},
]

WIDTHS = [724, 900, 1440, 1920]
THEMES = ["dark", "light"]

# 关键计算样式属性（覆盖颜色 / 字体 / 间距 / 尺寸 / 圆角 / 阴影 / 布局 / 溢出）
PROPS = [
    "display", "position", "visibility", "opacity", "overflow-x", "overflow-y",
    "width", "height", "min-width", "min-height", "max-width", "max-height",
    "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding-top", "padding-right", "padding-bottom", "padding-left",
    "gap", "row-gap", "column-gap",
    "flex-direction", "flex-wrap", "align-items", "justify-content",
    "grid-template-columns",
    "font-family", "font-size", "font-weight", "line-height", "letter-spacing",
    "text-align", "text-transform", "white-space", "text-overflow",
    "color", "background-color", "background-image",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-top-style", "border-radius",
    "box-shadow", "outline-color", "outline-width", "z-index",
]

COLLECT_JS = """
(props) => {
  const pathOf = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 12) {
      const parent = cur.parentElement;
      let idx = 1;
      if (parent) {
        let sib = cur;
        while ((sib = sib.previousElementSibling)) {
          if (sib.tagName === cur.tagName) idx += 1;
        }
      }
      parts.unshift(cur.tagName.toLowerCase() + ':' + idx);
      cur = parent;
    }
    return parts.join('>');
  };
  const nodes = [];
  const all = document.querySelectorAll('body *');
  const overflow = [];
  const collapsed = [];
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none') continue;
    const r = el.getBoundingClientRect();
    const style = {};
    for (const p of props) style[p] = cs.getPropertyValue(p);
    nodes.push({
      path: pathOf(el),
      tag: el.tagName.toLowerCase(),
      cls: el.getAttribute('class') || '',
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      style,
    });
    // 硬判据 1：文本容器内容被截断（有直接文本且 scrollWidth 超出 clientWidth）
    const directText = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
    if (directText && el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'auto'
        && cs.overflowX !== 'scroll' && cs.textOverflow !== 'ellipsis') {
      overflow.push({path: pathOf(el), tag: el.tagName.toLowerCase(),
                     text: directText.slice(0, 40),
                     scrollWidth: el.scrollWidth, clientWidth: el.clientWidth});
    }
    // 硬判据 2：按钮/控件塌陷（可见但宽或高小于 8px）
    if ((el.tagName === 'BUTTON' || el.getAttribute('role') === 'button')
        && (r.width < 8 || r.height < 8)) {
      collapsed.push({path: pathOf(el), tag: el.tagName.toLowerCase(),
                      rect: [Math.round(r.width), Math.round(r.height)],
                      text: (el.textContent || '').trim().slice(0, 30)});
    }
  }
  return {
    title: document.title,
    bodyClass: document.body.className,
    dataTheme: document.documentElement.getAttribute('data-theme'),
    docScrollWidth: document.documentElement.scrollWidth,
    docClientWidth: document.documentElement.clientWidth,
    nodes, overflow, collapsed,
  };
}
"""

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


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "before"
    only = [name for name in os.environ.get("CAPTURE_ONLY", "").split(",") if name]
    out = QA_ROOT / label
    (out / "shots").mkdir(parents=True, exist_ok=True)

    active_pages = [spec for spec in PAGES if not only or spec["name"] in only]
    assert active_pages, f"CAPTURE_ONLY={only} 未匹配任何目标"

    summary: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})

        # 真登录：gw_session 是 HttpOnly，必须在页面上下文内 fetch
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        status = page.evaluate(LOGIN_JS, [USERNAME, PASSWORD])
        print(f"login status = {status}")
        assert status == 200, f"登录失败，status={status}"
        page.close()

        for theme in THEMES:
            # 主题由 localStorage 决定，先写入再进入各页
            seed = ctx.new_page()
            seed.goto(BASE + "/static/embeds/canvas-list.html", wait_until="domcontentloaded")
            seed.evaluate(
                "t => { localStorage.setItem('studio_theme', t);"
                " localStorage.setItem('canvas_theme', t);"
                " localStorage.setItem('theme', t); }",
                theme,
            )
            seed.close()

            for width in WIDTHS:
                for spec in active_pages:
                    name, url = spec["name"], spec["url"]
                    page = ctx.new_page()
                    page.set_viewport_size({"width": width, "height": 900})
                    errors: list[str] = []
                    page.on("pageerror", lambda e: errors.append(str(e)[:300]))
                    page.on(
                        "console",
                        lambda m: errors.append("console:" + m.text[:200])
                        if m.type == "error"
                        else None,
                    )
                    page.goto(BASE + url, wait_until="networkidle", timeout=45000)
                    page.wait_for_timeout(1200)
                    if spec.get("interact"):
                        forced = page.evaluate(spec["interact"])
                        page.wait_for_timeout(400)
                    else:
                        forced = None
                    data = page.evaluate(COLLECT_JS, PROPS)
                    data["interact"] = forced
                    shot = out / "shots" / f"{name}__{width}__{theme}.png"
                    page.screenshot(path=str(shot), full_page=False)
                    data.update(
                        page=name, width=width, theme=theme,
                        url=url, errors=errors, shot=shot.name,
                    )
                    summary.append(data)
                    print(
                        f"{theme:5s} {width:5d} {name:20s} nodes={len(data['nodes']):4d} "
                        f"overflow={len(data['overflow']):2d} collapsed={len(data['collapsed'])} "
                        f"errors={len(errors)} docW={data['docScrollWidth']}/{data['docClientWidth']}"
                    )
                    page.close()
        browser.close()

    target = out / "snapshot.json"
    if only and target.exists():
        kept = [
            row for row in json.loads(target.read_text(encoding="utf-8"))
            if row["page"] not in {spec["name"] for spec in active_pages}
        ]
        summary = kept + summary
    target.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print(f"\n写入 {out / 'snapshot.json'}（{len(summary)} 组）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
