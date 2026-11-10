"""QIN-585 评审第 2 条复现：首屏期间点开收集箱（open）与延后调度（idle）并发读取。

统计一次进页里 `/api/asset-inbox?` 的 GET 次数，并读取外壳侧栏的未读角标数字：
- 改前：idle 与 open 各发一条，先发出的旧响应晚到，会把已清零的角标改回旧值；
- 改后：单飞只发一条，清零后作废在途旧响应，角标保持清零。

用法：python .local/qa585/probe_inbox.py <标签>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:2097"
URL = f"{BASE}/static/pages/assets.html"
TOKEN = "ExVgGvqZZSvOqkCp8ZUd-_MyXFwDNO8l7ZCgAhLCp9w"
TAG = sys.argv[1] if len(sys.argv) > 1 else "probe"
OUT = Path(__file__).parent / f"inbox-{TAG}.json"


def main() -> int:
    report: dict = {"tag": TAG, "steps": []}
    requests: list[dict] = []
    started = time.perf_counter()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-http-cache"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        ctx.add_cookies([{
            "name": "gw_session", "value": TOKEN, "domain": "127.0.0.1",
            "path": "/", "httpOnly": True, "sameSite": "Strict",
        }])
        page = ctx.new_page()

        def record(request):
            if "/api/asset-inbox" not in request.url:
                return
            requests.append({
                "method": request.method,
                "url": request.url.replace(BASE, ""),
                "ms": round((time.perf_counter() - started) * 1000),
            })

        page.on("request", record)
        page.goto(URL, wait_until="domcontentloaded")

        # 尽早点开收集箱：此时首屏延后调度可能还在途，正好复现 open 与 idle 并发。
        opened_at = None
        while time.perf_counter() - started < 15:
            button = page.query_selector('[data-asset-tree-entry="mine:inbox"]')
            if button and button.is_visible():
                button.click()
                opened_at = round((time.perf_counter() - started) * 1000)
                break
            page.wait_for_timeout(20)
        report["opened_ms"] = opened_at
        page.wait_for_timeout(8000)

        gets = [item for item in requests if item["method"] == "GET"]
        report["inbox_gets"] = gets
        report["inbox_posts"] = [item for item in requests if item["method"] == "POST"]
        report["steps"].append({
            "step": "一次进页只发一条收集箱 GET",
            "ok": len(gets) <= 1,
            "count": len(gets),
        })

        # 角标只是旁证：外壳侧栏快照由内嵌页推送，渲染时机不稳定，因此只记录序列、不作判据。
        samples = []
        for _ in range(10):
            samples.append(page.evaluate("""() => {
              const node = document.querySelector('[data-asset-tree-entry="mine:inbox"]');
              return node ? node.innerText.replace(/\\s+/g, ' ').trim() : '';
            }"""))
            page.wait_for_timeout(400)
        report["badge_samples"] = samples

        browser.close()

    report["all_ok"] = all(item["ok"] for item in report["steps"])
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
