"""QIN-645 asset-share 内容态取证。

asset-share 页的真实可达入口是 `/share/<token>`（后端返回页面外壳），
空实例无分享记录时只能采到提示态、DOM 仅 7 个节点，不足以验证样式迁移。
本脚本用 page.route 伪造公开分享 API 的**契约形状响应**驱动内容态，
迁出前后使用完全相同的注入点与响应体（响应体 SHA 打印留存），
保证对照有效；不代表真实分享数据链路可用性。

用法：python capture_share.py before|after
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import os

BASE = os.environ.get("CAPTURE_BASE", "http://127.0.0.1:2088")
USERNAME = "qa_local"
PASSWORD = "QaLocal#645pw"
TOKEN = "faketoken645"
QA_ROOT = Path(__file__).resolve().parent
WIDTHS = [724, 900, 1440, 1920]

META = {
    "share_id": "shr-fake-645",
    "title": "QIN-645 样式迁移取证",
    "available": True,
    "requires_password": False,
    "expires_at": None,
    "can_comment": True,
    "can_download": True,
}

ASSETS = [
    {
        "id": "ast-fake-001",
        "asset_id": "ast-fake-001",
        "name": "镜头 01 概念图",
        "kind": "IMAGE",
        "media_url": "/api/public/shares/faketoken645/assets/ast-fake-001/media",
        "review": {"comments": [], "approvals": []},
    },
    {
        "id": "ast-fake-002",
        "asset_id": "ast-fake-002",
        "name": "镜头 02 动态预览",
        "kind": "VIDEO",
        "media_url": "/api/public/shares/faketoken645/assets/ast-fake-002/media",
        "review": {"comments": [], "approvals": []},
    },
    {
        "id": "ast-fake-003",
        "asset_id": "ast-fake-003",
        "name": "配音轨 001 对白",
        "kind": "AUDIO",
        "media_url": "/api/public/shares/faketoken645/assets/ast-fake-003/media",
        "review": {"comments": [], "approvals": []},
    },
]

ACCESS = {
    "share": {
        "share_id": "shr-fake-645",
        "title": "QIN-645 样式迁移取证",
        "expires_at": None,
        "watermark_text": "仅供审阅",
        "permissions": {"comment": True, "download": True},
    },
    "assets": ASSETS,
    "data_gaps": [],
    "data_status": "ok",
    "ticket": "faketicket645",
}

COLLECT_JS = """
(props) => {
  const pathOf = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 12) {
      const parent = cur.parentElement;
      let idx = 1;
      if (parent) { let sib = cur; while ((sib = sib.previousElementSibling)) { if (sib.tagName === cur.tagName) idx += 1; } }
      parts.unshift(cur.tagName.toLowerCase() + ':' + idx);
      cur = parent;
    }
    return parts.join('>');
  };
  const nodes = [], overflow = [], collapsed = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none') continue;
    const r = el.getBoundingClientRect();
    const style = {};
    for (const p of props) style[p] = cs.getPropertyValue(p);
    nodes.push({path: pathOf(el), tag: el.tagName.toLowerCase(),
                cls: el.getAttribute('class') || '',
                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                style});
    const directText = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
    if (directText && el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'auto'
        && cs.overflowX !== 'scroll' && cs.textOverflow !== 'ellipsis') {
      overflow.push({path: pathOf(el), text: directText.slice(0, 40),
                     scrollWidth: el.scrollWidth, clientWidth: el.clientWidth});
    }
    if ((el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') && (r.width < 8 || r.height < 8)) {
      collapsed.push({path: pathOf(el), rect: [Math.round(r.width), Math.round(r.height)],
                      text: (el.textContent || '').trim().slice(0, 30)});
    }
  }
  return {title: document.title, bodyClass: document.body.className,
          dataTheme: document.documentElement.getAttribute('data-theme'),
          docScrollWidth: document.documentElement.scrollWidth,
          docClientWidth: document.documentElement.clientWidth,
          nodes, overflow, collapsed};
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

PROPS = [
    "display", "position", "visibility", "opacity", "overflow-x", "overflow-y",
    "width", "height", "min-width", "min-height", "max-width", "max-height",
    "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding-top", "padding-right", "padding-bottom", "padding-left",
    "gap", "row-gap", "column-gap",
    "flex-direction", "flex-wrap", "align-items", "justify-content",
    "grid-template-columns", "font-family", "font-size", "font-weight",
    "line-height", "letter-spacing", "text-align", "text-transform",
    "white-space", "text-overflow", "color", "background-color", "background-image",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-top-style", "border-radius", "box-shadow", "outline-color", "outline-width", "z-index",
]


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "before"
    out = QA_ROOT / label
    (out / "shots").mkdir(parents=True, exist_ok=True)

    meta_body = json.dumps(META, ensure_ascii=False).encode()
    access_body = json.dumps(ACCESS, ensure_ascii=False).encode()
    print(f"meta sha256={hashlib.sha256(meta_body).hexdigest()[:16]} "
          f"access sha256={hashlib.sha256(access_body).hexdigest()[:16]}")

    summary = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        assert page.evaluate(LOGIN_JS, [USERNAME, PASSWORD]) == 200
        page.close()

        for width in WIDTHS:
            for theme in ["dark", "light"]:
                seed = ctx.new_page()
                seed.goto(BASE + "/share/" + TOKEN, wait_until="domcontentloaded")
                seed.evaluate(
                    "t => { localStorage.setItem('studio_theme', t);"
                    " localStorage.setItem('canvas_theme', t);"
                    " localStorage.setItem('theme', t); }",
                    theme,
                )
                seed.close()

                pg = ctx.new_page()
                pg.set_viewport_size({"width": width, "height": 900})
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)[:300]))
                pg.on("console", lambda m: errors.append("console:" + m.text[:200])
                      if m.type == "error" else None)

                def handle(route):
                    url = route.request.url
                    if url.endswith("/access"):
                        route.fulfill(status=200, content_type="application/json", body=access_body)
                    elif "/assets/" in url and "/media" in url:
                        route.fulfill(status=404, content_type="application/json",
                                      body=b'{"detail":{"code":"MEDIA_UNAVAILABLE","message":"probe"}}')
                    elif "/api/public/shares/" in url:
                        route.fulfill(status=200, content_type="application/json", body=meta_body)
                    else:
                        route.continue_()

                pg.route("**/api/public/shares/**", handle)
                pg.goto(BASE + "/share/" + TOKEN, wait_until="networkidle", timeout=45000)
                pg.wait_for_timeout(1500)
                data = pg.evaluate(COLLECT_JS, PROPS)
                shot = out / "shots" / f"asset-share-content__{width}__{theme}.png"
                pg.screenshot(path=str(shot), full_page=False)
                data.update(page="asset-share-content", width=width, theme=theme,
                            url="/share/" + TOKEN, errors=errors, shot=shot.name)
                summary.append(data)
                print(f"{theme:5s} {width:5d} nodes={len(data['nodes']):4d} "
                      f"overflow={len(data['overflow']):2d} collapsed={len(data['collapsed'])} "
                      f"errors={len(errors)}")
                pg.close()
        browser.close()

    (out / "snapshot-share-content.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    print(f"写入 {out / 'snapshot-share-content.json'}（{len(summary)} 组）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
