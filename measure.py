"""QIN-585 首屏性能与视觉回归测量（本地证据，不入库）。

在约 1000 条素材的隔离实例上，禁用缓存打开资产库外壳页，测量：
- 首屏 JS/CSS 传输体积（区分「首卡可见前」与「整页））
- 第一张卡片可见耗时
- 首屏截图（用于改前/改后像素比对）

用法：python .local/qa585/measure.py <标签>
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:2097"
URL = f"{BASE}/static/pages/assets.html"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""
TAG = sys.argv[1] if len(sys.argv) > 1 else "run"
RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 5
CARD = ".registry-card[data-registry-card]"
OUT = Path(__file__).parent


def collect_assets(page) -> dict:
    """汇总所有 frame 的 js/css 传输体积与请求清单。"""
    script = """
    () => {
      const out = [];
      for (const e of performance.getEntriesByType('resource')) {
        const url = e.name;
        const isJs = /\\.m?js(\\?|$)/.test(url) || e.initiatorType === 'script';
        const isCss = /\\.css(\\?|$)/.test(url) || e.initiatorType === 'link';
        if (!isJs && !isCss) continue;
        out.push({url, type: isJs ? 'js' : 'css',
                  transfer: e.transferSize || 0, decoded: e.decodedBodySize || 0});
      }
      return out;
    }
    """
    entries = []
    for frame in page.frames:
        try:
            entries.extend(frame.evaluate(script))
        except Exception:
            continue
    # 同 URL 只计一次（iframe 重复请求极罕见）
    seen, uniq = set(), []
    for item in entries:
        key = (item["url"], item["type"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    js = sum(i["transfer"] for i in uniq if i["type"] == "js")
    css = sum(i["transfer"] for i in uniq if i["type"] == "css")
    return {"js": js, "css": css, "count": len(uniq), "entries": uniq}


def once(pw, index: int) -> dict:
    browser = pw.chromium.launch(args=["--disable-http-cache"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    ctx.add_cookies(
        [
            {
                "name": "gw_session",
                "value": TOKEN,
                "domain": "127.0.0.1",
                "path": "/",
                "httpOnly": True,
                "sameSite": "Strict",
            }
        ]
    )
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    started = time.perf_counter()
    page.goto(URL, wait_until="commit")
    first_card = None
    while time.perf_counter() - started < 45:
        for frame in page.frames:
            try:
                el = frame.query_selector(CARD)
                if el and el.is_visible():
                    first_card = time.perf_counter() - started
                    break
            except Exception:
                continue
        if first_card:
            break
        time.sleep(0.02)
    at_first = collect_assets(page) if first_card else {"js": 0, "css": 0, "count": 0}
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    final = collect_assets(page)
    shot = OUT / f"{TAG}-{index}.png"
    page.screenshot(path=str(shot), full_page=False)
    cards = 0
    for frame in page.frames:
        try:
            cards = max(cards, len(frame.query_selector_all(CARD)))
        except Exception:
            continue
    browser.close()
    return {
        "first_card_ms": round((first_card or -1) * 1000, 1),
        "js_at_first": at_first["js"],
        "css_at_first": at_first["css"],
        "js_total": final["js"],
        "css_total": final["css"],
        "req_total": final["count"],
        "cards": cards,
        "errors": errors,
        "entries": final["entries"],
    }


def main() -> None:
    results = []
    with sync_playwright() as pw:
        for i in range(RUNS):
            results.append(once(pw, i))
            print(f"  run{i + 1}: {results[-1]['first_card_ms']}ms "
                  f"js={results[-1]['js_total']} css={results[-1]['css_total']} "
                  f"cards={results[-1]['cards']}")
    summary = {
        "tag": TAG,
        "runs": results,
        "median_first_card_ms": statistics.median(r["first_card_ms"] for r in results),
        "median_js_total": statistics.median(r["js_total"] for r in results),
        "median_css_total": statistics.median(r["css_total"] for r in results),
        "median_js_at_first": statistics.median(r["js_at_first"] for r in results),
        "errors": sorted({e for r in results for e in r["errors"]}),
    }
    (OUT / f"{TAG}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
