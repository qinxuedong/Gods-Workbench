"""QIN-585 延后功能可用性验证（本地证据，不入库）。

首屏延后块把收集箱与治理建议放到就绪后的空闲时段，本脚本验证：
- 首卡画出后，治理建议入口仍能打开抽屉，抽屉里有真实分组内容（不是空壳/报错态）；
- 收集箱入口仍能切换并渲染列表；
- 全程无 console / pageerror。

用法：python .local/qa585/verify_deferred.py <token>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:2097"
URL = f"{BASE}/static/pages/assets.html"
TOKEN = sys.argv[1]
CARD = ".registry-card[data-registry-card]"
OUT = Path(__file__).parent

report: dict = {"steps": [], "console_errors": [], "page_errors": []}


def embed_page(page):
    # iframe 由外壳脚本较晚挂上，直接等元素再取 content_frame 比遍历 page.frames 稳。
    try:
        handle = page.wait_for_selector("#assetVaultFrame", timeout=20000)
        frame = handle.content_frame()
        if frame and "asset-manager.html" in frame.url:
            return frame
    except Exception:
        pass
    for frame in page.frames:
        if "asset-manager.html" in frame.url:
            return frame
    return None


with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--disable-http-cache"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    ctx.add_cookies([{"name": "gw_session", "value": TOKEN, "domain": "127.0.0.1",
                      "path": "/", "httpOnly": True, "sameSite": "Strict"}])
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and report["console_errors"].append(m.text[:200]))
    page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:200]))

    # 记录两条延后链路的网络时序：首卡之后才发起 = 延后生效；仍 200 = 功能没被砍断。
    deferred_requests: dict[str, dict] = {}

    def on_response(resp):
        for key in ("/api/asset-inbox", "/api/asset-registry/governance/suggestions"):
            if key in resp.url and key not in deferred_requests:
                try:
                    req = resp.request
                    started = req.timing.get("startTime") or 0
                    deferred_requests[key] = {"status": resp.status,
                                              "start_ms": round(started, 1)}
                except Exception:
                    deferred_requests[key] = {"status": resp.status, "start_ms": None}

    page.on("response", on_response)

    started = time.perf_counter()
    page.goto(URL, wait_until="commit")
    frame = embed_page(page)
    first = None
    while time.perf_counter() - started < 45:
        if frame:
            try:
                el = frame.query_selector(CARD)
                if el and el.is_visible():
                    first = time.perf_counter() - started
                    break
            except Exception:
                frame = embed_page(page)
        else:
            frame = embed_page(page)
        time.sleep(0.02)
    report["first_card_ms"] = round((first or -1) * 1000, 1)
    report["steps"].append({"step": "首卡可见", "ok": bool(first), "ms": report["first_card_ms"]})

    # 首屏刚出来时，延后块可能尚未完成；等治理徽标出现（说明延后请求已回来）
    badge_filled = False
    for _ in range(120):
        total = frame.query_selector("[data-governance-total]")
        if total and (total.inner_text() or "").strip() not in {"", "—", "…"}:
            badge_filled = True
            report["governance_badge"] = total.inner_text().strip()
            break
        time.sleep(0.25)
    report["steps"].append({"step": "延后后治理徽标回填", "ok": badge_filled,
                            "value": report.get("governance_badge")})

    trigger = frame.query_selector("[data-governance-open]")
    report["steps"].append({"step": "治理入口存在", "ok": bool(trigger)})
    if trigger:
        trigger.click()
        page.wait_for_timeout(900)
        dialog = frame.query_selector('[role="dialog"][aria-labelledby="governanceDrawerTitle"]')
        body_text = ""
        if dialog:
            body_text = (dialog.inner_text() or "").strip()
        report["steps"].append({
            "step": "治理抽屉打开且有内容",
            "ok": bool(dialog) and len(body_text) > 40,
            "chars": len(body_text),
            "preview": body_text[:120],
        })
        frame.query_selector('.governance-drawer [data-governance-close], [data-governance-close]') and \
            frame.query_selector('[data-governance-close]').click()
        page.wait_for_timeout(500)

    # 收集箱：入口在外壳页左栏（QIN-566 收敛后的唯一侧栏），按可见文案定位最稳。
    inbox_ok = False
    inbox_detail = ""
    clicked = False
    try:
        page.get_by_role("button", name="收集箱", exact=False).first.click(timeout=8000)
        clicked = True
    except Exception:
        try:
            page.locator(".asset-shell-nav-group-items >> text=收集箱").first.click(timeout=5000)
            clicked = True
        except Exception:
            clicked = False
    if clicked:
        page.wait_for_timeout(1800)
        state = frame.evaluate("""() => {
          const cards = document.querySelectorAll('[data-inbox-card]').length;
          // 本实例收集箱为空的，因此「视图已切过去」才是判据：出现收集箱面板/空态/标题。
          const panel = Boolean(document.querySelector('[data-inbox-settings-panel], .inbox-empty, .inbox-settings'));
          return {cards, panel, text: document.body.innerText.includes('收集箱')};
        }""")
        inbox_ok = bool(state.get("panel")) or bool(state.get("text"))
        inbox_detail = f"cards={state.get('cards')} panel={state.get('panel')} text={state.get('text')}"
    report["steps"].append({"step": "收集箱可用", "ok": inbox_ok, "detail": inbox_detail})

    # 延后链路时序判据：首屏列表请求必须**先跑完**，收集箱/治理建议才允许发起。
    # 与首卡「可见」时刻比较会有 20ms 轮询与合帧误差，用列表 responseEnd 更准。
    timeline = frame.evaluate("""() => {
      const out = {firstPageEnd: null, deferred: []};
      for(const e of performance.getEntriesByType('resource')){
        if(e.name.includes('/api/asset-registry/assets?') && out.firstPageEnd === null){
          out.firstPageEnd = Math.round(e.responseStart ? e.responseEnd : e.responseEnd);
        }
        if(e.name.includes('/api/asset-inbox') || e.name.includes('governance/suggestions')){
          out.deferred.push({url: e.name.replace(location.origin, '').split('?')[0],
                             start: Math.round(e.startTime), end: Math.round(e.responseEnd)});
        }
      }
      return out;
    }""")
    report["first_page_request_end_ms"] = timeline["firstPageEnd"]
    report["first_card_ms_measured"] = report["first_card_ms"]
    for item in timeline["deferred"]:
        report["steps"].append({
            "step": f"延后链路 {item['url']}",
            "ok": timeline["firstPageEnd"] is not None and item["start"] >= timeline["firstPageEnd"],
            "start_ms": item["start"],
            "first_page_end_ms": timeline["firstPageEnd"],
            "first_card_ms": report["first_card_ms"],
        })

    page.screenshot(path=str(OUT / "deferred-functions.png"))
    (OUT / "deferred-verify.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    browser.close()
