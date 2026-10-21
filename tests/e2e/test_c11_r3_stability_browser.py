"""C11-T5：R3 长时间浏览器试运行的真实观测门禁。

默认只验证真实浏览器门禁与报告口径。长跑必须由操作者显式设置
``GW_C11_REAL_E2E=1``；未开启或本机没有 Chrome 时跳过，不能把环境缺失
写成稳定版通过。测试只记录路由往返、任务轮询、DOM/JS 堆观测和页面截图，
阈值与稳定版资格保留为待用户确认。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from workflow_support import settings_browser

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C11-evidence"
ROUTES = (
    "/static/pages/projects.html",
    "/static/pages/assets.html",
    "/static/pages/agents.html",
    "/static/pages/storyboard.html?view=storyboard",
)
MIN_ROUTE_ROUNDTRIPS = 500
MIN_TASK_POLLS = 500
POLL_PATH = "/api/observability/tasks?limit=20"


def _real_run_enabled(environ: dict[str, str] | None = None) -> bool:
    """真实长跑只能由操作者显式开启。"""
    env = os.environ if environ is None else environ
    return str(env.get("GW_C11_REAL_E2E", "")).strip().lower() in {"1", "true", "yes"}


def _evidence_dir() -> Path:
    configured = str(os.environ.get("GW_C11_EVIDENCE_DIR", "")).strip()
    path = Path(configured).expanduser() if configured else EVIDENCE_ROOT
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _snapshot(page: Any) -> dict[str, Any]:
    """读取浏览器可提供的真实资源观测；不可用指标保持 null。"""
    return page.evaluate(
        """() => {
          const memory = performance && performance.memory ? performance.memory : null;
          return {
            dom_nodes: document.querySelectorAll('*').length,
            resource_entries: performance.getEntriesByType('resource').length,
            js_heap_used_bytes: memory ? memory.usedJSHeapSize : null,
            js_heap_total_bytes: memory ? memory.totalJSHeapSize : null,
            js_heap_limit_bytes: memory ? memory.jsHeapSizeLimit : null,
            ready_state: document.readyState,
            path: window.location.pathname,
          };
        }"""
    )


def _response_record(response: Any, path: str) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception:
        body = None
    return {
        "status": response.status,
        "content_type": response.headers.get("content-type", ""),
        "data_status": body.get("data_status") if isinstance(body, dict) else None,
        "item_count": len(body.get("items", []))
        if isinstance(body, dict) and isinstance(body.get("items"), list)
        else None,
        "path": path,
    }


def _goto_stability_route(page: Any, url: str) -> None:
    """完成一次真实页面路由往返；不等待网络空闲以避免页面轮询拖长长跑。"""
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function("!window.GWAuthGate || window.GWAuthGate.loaded === true")


def test_c11_real_browser_gate_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GW_C11_REAL_E2E", raising=False)
    assert _real_run_enabled() is False


def test_c11_stability_contract_keeps_thresholds_pending_user_confirmation() -> None:
    report = {
        "qualification": {
            "status": "pending_user_confirmation",
            "thresholds": None,
            "note": "只记录本机观察值；稳定版资格与阈值由秦先生审核",
        },
        "minimum_route_roundtrips": MIN_ROUTE_ROUNDTRIPS,
        "minimum_task_polls": MIN_TASK_POLLS,
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert report["qualification"]["thresholds"] is None
    assert "达标" not in serialized
    assert "已通过" not in serialized
    assert MIN_ROUTE_ROUNDTRIPS >= 500
    assert MIN_TASK_POLLS >= 500


def test_c11_r3_stability_real_browser_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """执行 500 次页面路由往返与 500 次真实任务投影轮询并留证。"""
    if not _real_run_enabled():
        pytest.skip("C11 真实长跑默认关闭；设置 GW_C11_REAL_E2E=1 后才会运行")
    browser_api = pytest.importorskip("playwright.sync_api")

    responses: list[dict[str, Any]] = []
    poll_records: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    screenshots: list[str] = []
    route_errors: list[str] = []
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):

        def capture(response: Any) -> None:
            if "/api/observability/tasks" not in response.url:
                return
            responses.append(
                _response_record(response, response.url.split(base, 1)[-1])
            )

        page.context.on("response", capture)
        for index in range(MIN_ROUTE_ROUNDTRIPS):
            route = ROUTES[index % len(ROUTES)]
            try:
                _goto_stability_route(page, base + route)
            except Exception as exc:
                route_errors.append(f"{index}:{route}:{exc}")
                raise

            poll = page.context.request.get(base + POLL_PATH, headers={"Origin": base})
            assert poll.status == 200, poll.text()
            body = poll.json()
            assert isinstance(body, dict)
            assert isinstance(body.get("items"), list)
            assert body.get("data_status") in {
                "ok",
                "complete",
                "degraded",
                "not_integrated",
            }
            poll_records.append(_response_record(poll, POLL_PATH))

            if index in {0, 249, 499}:
                sample = _snapshot(page)
                sample.update({"iteration": index + 1, "poll_status": poll.status})
                samples.append(sample)
            if index == 0:
                shot = _evidence_dir() / "c11-t5-route-start.png"
                page.screenshot(path=str(shot), full_page=True)
                screenshots.append(shot.name)
            if index == 249:
                shot = _evidence_dir() / "c11-t5-route-mid.png"
                page.screenshot(path=str(shot), full_page=True)
                screenshots.append(shot.name)
            if index == 499:
                shot = _evidence_dir() / "c11-t5-route-end.png"
                page.screenshot(path=str(shot), full_page=True)
                screenshots.append(shot.name)

        assert len(poll_records) >= MIN_TASK_POLLS
        assert all(record["status"] == 200 for record in poll_records)
        assert not errors, errors
        assert not route_errors, route_errors

    report = {
        "task": "QIN-205 / C11-T5",
        "commit_sha": _git_sha(),
        "browser": {"engine": "Chrome via Playwright", "real_opt_in": True},
        "fixture": {
            "seed": "c11-t5-stability-v001",
            "route_roundtrips": MIN_ROUTE_ROUNDTRIPS,
            "task_polls": len(poll_records),
            "routes_sha256": hashlib.sha256(
                "\n".join(ROUTES).encode("utf-8")
            ).hexdigest(),
        },
        "observations": {
            "samples": samples,
            "task_poll_responses": poll_records[-10:],
            "browser_response_events": responses[-10:],
            "page_errors": errors,
            "route_errors": route_errors,
        },
        "screenshots": screenshots,
        "qualification": {
            "status": "pending_user_confirmation",
            "thresholds": None,
            "note": "真实观测已记录；内存、DOM、句柄阈值与 R3 资格需秦先生审核",
        },
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "达标" not in serialized
    assert "已通过" not in serialized
    _write_json(_evidence_dir() / "c11-t5-stability-report.json", report)
    (_evidence_dir() / "c11-r3-stability-whitepaper.md").write_text(
        "# Gods' Workbench R3 稳定版资质审查报告\n\n"
        "状态：待秦先生审核。\n\n"
        f"- Commit：`{report['commit_sha']}`\n"
        f"- 路由往返：{MIN_ROUTE_ROUNDTRIPS}\n"
        f"- 任务轮询：{len(poll_records)}\n"
        "- 观测文件：`c11-t5-stability-report.json`\n"
        "- 截图：`" + "`、`".join(screenshots) + "`\n"
        "- 阈值：待用户确认，未作稳定版资格结论。\n",
        encoding="utf-8",
    )
