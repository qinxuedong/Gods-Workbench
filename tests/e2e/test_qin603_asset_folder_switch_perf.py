"""QIN-603 退出脚本：点击文件夹的重绘次数、目录索引请求、热切换缓存与慢响应。

只做真实浏览器验收，不改生产实现。必须显式设置 ``GW_C10_REAL_E2E=1``；本机没有可用
Chrome 时跳过——环境缺失不能记成产品通过。

观测口径与 AC0 基线一致（见 Issue #713 基线评论）：
* render 次数 = 内嵌页管理根里 ``.asset-content`` 元素身份变化次数（不是数 render() 调用）；
* 稳定耗时用页内 ``performance.now()``；长任务用 ``PerformanceObserver(longtask)``；
* 入口是真实用户入口：外壳资产页侧栏 ``[data-asset-tree-entry="mine:root:<id>"]``。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
AUTH = {
    "Authorization": "Bearer cleanroom-test",
    "X-User-Role": "editor",
    "Origin": "http://testserver",
}
REGISTRY = "/api/asset-registry"
ROOTS = {"root_a": "QIN603甲库", "root_b": "QIN603乙库"}
PER_FOLDER = 120
"""AC5 阈值（产品在 #713 已确认）：render ≤2；稳定耗时中位 ≤210ms、p95 ≤1250ms；
长任务 p95 总时长 ≤65ms。这里留一点浏览器抖动余量，用放大幅度而不是放宽指标。"""
MAX_RENDERS = 2
MAX_SETTLE_MEDIAN_MS = 210.0
MAX_SETTLE_P95_MS = 1250.0
MAX_LONGTASK_P95_MS = 65.0


def _real_run_enabled() -> bool:
    return str(os.environ.get("GW_C10_REAL_E2E", "")).strip().lower() in {"1", "true", "yes"}


INSTRUMENT = """
() => {
    const root = document.getElementById('assetManagerRoot');
    const state = window.__qin603 = window.__qin603 || {};
    state.renders = 0; state.lastMutation = performance.now();
    state.longTasks = 0; state.longTaskMs = 0; state.started = performance.now();
    let lastContent = root.querySelector('.asset-content');
    if (!state.observer) {
        state.observer = new MutationObserver(() => {
            state.lastMutation = performance.now();
            const current = root.querySelector('.asset-content');
            if (current !== lastContent) { lastContent = current; state.renders += 1; }
        });
        state.observer.observe(root, {childList: true, subtree: true});
    }
    if (!state.perfObserver) {
        state.perfObserver = new PerformanceObserver(list => {
            for (const entry of list.getEntries()) { state.longTasks += 1; state.longTaskMs += entry.duration; }
        });
        state.perfObserver.observe({entryTypes: ['longtask']});
    }
    return true;
}
"""

RESET = """
() => { const s = window.__qin603;
    s.renders = 0; s.lastMutation = performance.now();
    s.longTasks = 0; s.longTaskMs = 0; s.started = performance.now(); }
"""

SNAPSHOT = """
() => { const s = window.__qin603;
    return {renders: s.renders, lastMutation: s.lastMutation, started: s.started,
            longTasks: s.longTasks, longTaskMs: s.longTaskMs,
            cards: document.querySelectorAll('[data-registry-card]').length}; }
"""


def _seed_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """真实落盘 + 产品自己的 reindex 链路造数（不走任何测试后门）。"""
    allowed = tmp_path / "allowed"
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_TEST_ALLOW_CSP_UNSAFE_EVAL", "1")
    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "1")

    for root_id in ROOTS:
        folder = allowed / root_id / "set-1"
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(PER_FOLDER):
            (folder / f"shot-{index:04d}.png").write_bytes(PNG_1X1)

    from fastapi.testclient import TestClient

    from gw.api.app import create_app
    from gw.asset_registry import index_jobs

    if index_jobs._default is not None:
        index_jobs._default.shutdown()
        index_jobs._default = None

    with TestClient(create_app()) as client:
        for order, root_id in enumerate(ROOTS):
            revision = client.get("/api/storage-settings", headers=AUTH).json().get("revision", 1)
            submitted = client.post(
                f"{REGISTRY}/reindex",
                headers={**AUTH, "Idempotency-Key": f"qin603-e2e-{root_id}-{order}-{int(time.time())}"},
                json={
                    "background": True,
                    "hash_files": False,
                    "max_files": 20000,
                    "roots": [root_id],
                    "register_root": {
                        "id": root_id,
                        "name": ROOTS[root_id],
                        "path": str(allowed / root_id),
                        "expected_version": revision,
                    },
                },
            )
            assert submitted.status_code == 202, submitted.text[:400]
            job_id = submitted.json()["job_id"]
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                job = client.get(f"{REGISTRY}/workspace-jobs/{job_id}", headers=AUTH).json()["job"]
                if job["status"] in index_jobs.TERMINAL:
                    assert job["status"] == "succeeded", job
                    break
                time.sleep(0.2)
            else:  # pragma: no cover - 超时即环境问题
                raise AssertionError("索引任务未在 600s 内终结")
    # 造数用 dev 头认证（local），浏览器验收必须走真实登录（local_account），
    # 否则 /api/asset-auth/local/login 直接 401，外壳拿不到挂载根。
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    return allowed


def _start_server() -> tuple[Any, str, Any]:
    import uvicorn

    from gw.api.app import create_app

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "隔离服务未启动"

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=30)

    return server, f"http://127.0.0.1:{port}", stop


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _count_requests(requests: list[dict], since: float) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in requests:
        if entry["ts"] < since:
            continue
        url = entry["url"]
        if f"{REGISTRY}/assets?" in url and "project_id=" in url:
            key = "assets_project_count"
        elif f"{REGISTRY}/assets" in url:
            key = "assets"
        elif f"{REGISTRY}/folders" in url:
            key = "folders"
        elif f"{REGISTRY}/status" in url:
            key = "status"
        elif f"{REGISTRY}/asset-structures" in url:
            key = "structures"
        else:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


@pytest.mark.skipif(not _real_run_enabled(), reason="需要 GW_C10_REAL_E2E=1 显式开启真实浏览器验收")
def test_qin603_folder_switch_render_and_requests(tmp_path, monkeypatch):
    browser_api = pytest.importorskip("playwright.sync_api")
    from playwright_support import open_playwright

    _seed_workspace(tmp_path, monkeypatch)
    _, base, stop = _start_server()
    requests: list[dict] = []
    samples: list[dict] = []
    page_errors: list[str] = []
    try:
        with open_playwright(browser_api) as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                login = context.request.post(
                    base + "/api/asset-auth/local/login",
                    data={"username": "qin603_e2e", "password": "Test2026"},
                    headers={"Origin": base},
                )
                if login.status != 200:
                    setup = context.request.post(
                        base + "/api/asset-auth/local/setup",
                        data={"username": "qin603_e2e", "password": "Test2026"},
                        headers={"Origin": base},
                    )
                    assert setup.status in (201, 409), setup.text()
                page = context.new_page()
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on(
                    "request",
                    lambda r: requests.append({"url": r.url, "ts": time.time() * 1000}),
                )
                page.goto(base + "/static/pages/assets.html", wait_until="domcontentloaded", timeout=120000)
                deadline = time.monotonic() + 180
                entries = 0
                while time.monotonic() < deadline:
                    entries = len(page.query_selector_all('[data-asset-tree-entry^="mine:root:"]'))
                    if entries >= len(ROOTS):
                        break
                    page.evaluate(
                        "() => { const o = document.getElementById('assetSidebarOverview'); if (o) o.click(); }"
                    )
                    time.sleep(1)
                assert entries >= len(ROOTS), f"侧栏未出现挂载根（{entries}）"
                frame = next((f for f in page.frames if "asset-manager.html" in f.url), None)
                assert frame is not None, "未找到内嵌资产页"
                frame.evaluate(INSTRUMENT)

                def snapshot() -> dict:
                    return frame.evaluate(SNAPSHOT)

                def settle() -> dict:
                    previous = snapshot()
                    last_change = time.monotonic()
                    while time.monotonic() - last_change < 60:
                        time.sleep(0.05)
                        current = snapshot()
                        if (current["renders"], current["cards"]) != (previous["renders"], previous["cards"]):
                            previous = current
                            last_change = time.monotonic()
                            continue
                        if time.monotonic() - last_change >= 0.6:
                            break
                    return previous

                # 首屏链路静默后再开测，避免把引导成本算进一次点击
                frame.evaluate(RESET)
                settle()
                time.sleep(1.5)

                page.click('[data-asset-tree-entry="mine:root:root_a"]')
                settle()  # 预热一次，排除首次进入目录的建树成本
                current = "root_a"
                for index in range(6):
                    root_id = "root_b" if current == "root_a" else "root_a"
                    frame.evaluate(RESET)
                    marker = time.time() * 1000
                    page.click(f'[data-asset-tree-entry="mine:root:{root_id}"]')
                    state = settle()
                    samples.append({
                        "index": index,
                        "root": root_id,
                        "renders": state["renders"],
                        "settle_ms": round(max(0.0, state["lastMutation"] - state["started"]), 1),
                        "long_tasks": state["longTasks"],
                        "long_task_ms": round(state["longTaskMs"], 1),
                        "cards": state["cards"],
                        "requests": _count_requests(requests, marker),
                    })
                    current = root_id
            finally:
                browser.close()
    finally:
        stop()

    renders = [float(s["renders"]) for s in samples]
    settles = [s["settle_ms"] for s in samples]
    long_tasks = [s["long_task_ms"] for s in samples]
    folders = sum(s["requests"].get("folders", 0) for s in samples)
    project_counts = sum(s["requests"].get("assets_project_count", 0) for s in samples)
    # 6 次样本里第 2 次起都是「回到看过的文件夹」，列表请求必须是 0
    # 第 0 次样本是 root_b 的首次进入（未缓存，允许一次列表请求）；第 1 次起都是回访
    asset_refetch = sum(s["requests"].get("assets", 0) for s in samples[1:])

    evidence = {
        "ticket": "QIN-603",
        "datasets": {"roots": list(ROOTS), "per_folder": PER_FOLDER},
        "thresholds": {
            "max_renders": MAX_RENDERS,
            "max_settle_median_ms": MAX_SETTLE_MEDIAN_MS,
            "max_settle_p95_ms": MAX_SETTLE_P95_MS,
            "max_longtask_p95_ms": MAX_LONGTASK_P95_MS,
        },
        "samples": samples,
        "summary": {
            "renders_median": _median(renders),
            "renders_max": max(renders) if renders else 0,
            "settle_median_ms": round(_median(settles), 1),
            "settle_p95_ms": round(_percentile(settles, 0.95), 1),
            "longtask_p95_ms": round(_percentile(long_tasks, 0.95), 1),
            "folders_requests": folders,
            "project_count_requests": project_counts,
            "assets_requests_on_hot_switch": asset_refetch,
        },
        "page_errors": page_errors,
    }
    out = Path(os.environ.get("GW_QIN603_EVIDENCE", tmp_path / "qin603-e2e.json"))
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert page_errors == [], f"页面不得报错：{page_errors}"
    assert samples, "必须采到样本"
    assert max(renders) <= MAX_RENDERS, f"一次点击整页重绘必须 ≤{MAX_RENDERS}：{evidence['summary']}"
    assert evidence["summary"]["settle_median_ms"] <= MAX_SETTLE_MEDIAN_MS, evidence["summary"]
    assert evidence["summary"]["settle_p95_ms"] <= MAX_SETTLE_P95_MS, evidence["summary"]
    assert evidence["summary"]["longtask_p95_ms"] <= MAX_LONGTASK_P95_MS, evidence["summary"]
    assert folders == 0, f"筛选未变时切文件夹不得重拉 /folders（实际 {folders}）"
    assert project_counts == 0, f"筛选未变时不得重拉 /assets?project_id=（实际 {project_counts}）"
    assert asset_refetch == 0, f"热切换回到看过的文件夹不得重拉列表（实际 {asset_refetch}）"


@pytest.mark.skipif(not _real_run_enabled(), reason="需要 GW_C10_REAL_E2E=1 显式开启真实浏览器验收")
@pytest.mark.parametrize("delay_ms", [1500, 5000])
def test_qin603_slow_assets_keeps_loading_then_settles_once(tmp_path, monkeypatch, delay_ms):
    """异步-慢：/assets 人为延迟 1500ms / 5000ms 时，完成前保持加载态，完成后终态正确且 render ≤2。

    延迟必须用 async API 实现：sync API 的 page.route 里 sleep 会阻塞驱动事件循环，
    延迟不会真的生效（会把「没延迟」误记成「延迟下也达标」）。
    """
    import asyncio

    pytest.importorskip("playwright.sync_api")
    from playwright.async_api import async_playwright

    _seed_workspace(tmp_path, monkeypatch)
    _, base, stop = _start_server()

    async def scenario() -> dict:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel="chrome", headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 900})
                login = await context.request.post(
                    base + "/api/asset-auth/local/login",
                    data={"username": "qin603_e2e", "password": "Test2026"},
                    headers={"Origin": base},
                )
                if login.status != 200:
                    await context.request.post(
                        base + "/api/asset-auth/local/setup",
                        data={"username": "qin603_e2e", "password": "Test2026"},
                        headers={"Origin": base},
                    )
                page = await context.new_page()
                await page.goto(base + "/static/pages/assets.html", wait_until="domcontentloaded", timeout=120000)
                deadline = time.monotonic() + 180
                entries = 0
                while time.monotonic() < deadline:
                    entries = len(await page.query_selector_all('[data-asset-tree-entry^="mine:root:"]'))
                    if entries >= len(ROOTS):
                        break
                    await page.evaluate(
                        "() => { const o = document.getElementById('assetSidebarOverview'); if (o) o.click(); }"
                    )
                    await asyncio.sleep(1)
                assert entries >= len(ROOTS), f"侧栏未出现挂载根（{entries}）"
                frame = next((f for f in page.frames if "asset-manager.html" in f.url), None)
                assert frame is not None, "未找到内嵌资产页"
                await frame.evaluate(INSTRUMENT)
                # 首屏链路静默后再起测：否则引导期的重绘会被算进这一次点击
                await asyncio.sleep(1.5)
                await frame.evaluate(RESET)

                async def slow_list(route):
                    await asyncio.sleep(delay_ms / 1000)
                    await route.continue_()

                before = await frame.evaluate(SNAPSHOT)
                await page.route("**/api/asset-registry/assets?**", slow_list)
                await page.click('[data-asset-tree-entry="mine:root:root_a"]')
                await asyncio.sleep(min(0.4, delay_ms / 4000))
                mid = await frame.evaluate(
                    """() => { const root = document.getElementById('assetManagerRoot');
                        return {cards: root.querySelectorAll('[data-registry-card]').length,
                                loading: root.innerHTML.includes('正在读取')}; }"""
                )
                # 判据只看「完成前保持加载态、不提前标就绪」：加载态是否保留上一屏网格
                # 由既有实现决定（1500ms 与 5000ms 实测不同），不属于本票要固定的行为。
                assert mid["loading"] is True, f"列表未返回前必须处于加载态：{mid}"
                assert mid["cards"] == 0 or mid["cards"] == before["cards"], (
                    f"列表未返回前不得出现新目录的卡片：{before} -> {mid}"
                )

                state = {"cards": 0, "renders": 0}
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.2)
                    state = await frame.evaluate(SNAPSHOT)
                    if state["cards"] > 0:
                        break
                assert state["cards"] > 0, "延迟结束后必须落地终态卡片"
                await asyncio.sleep(1.0)
                return await frame.evaluate(SNAPSHOT)
            finally:
                await browser.close()

    try:
        final = asyncio.run(scenario())
    finally:
        stop()
    assert final["renders"] <= MAX_RENDERS, f"慢响应下仍必须 ≤{MAX_RENDERS} 次重绘：{final}"
    assert final["cards"] > 0, f"终态必须有卡片：{final}"
