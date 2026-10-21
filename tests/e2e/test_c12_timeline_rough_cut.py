"""C12-T5：时间线粗剪连播的真实浏览器收口门禁。

本测试只在操作者显式设置 ``GW_C12_REAL_E2E=1`` 时启动 Chrome 和隔离服务。
测试使用真实 FastAPI 路由、真实浏览器 DOM 与真实 JsonState 落盘；镜头图引用
使用固定的时间线清单 fixture，不调用视频 Provider，也不把 fixture 结果写成
Provider 真实成功。当前分支若尚未包含 C12-T3/T4，会生成阻塞证据并跳过真实路径。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright_support import open_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C12-evidence"
REAL_RUN_ENV = "GW_C12_REAL_E2E"
FIXTURE_ID = "c12-t5-storyboard-v001"
AUTH_HEADERS = {"Origin": "{base}"}


def _real_run_enabled(environ: dict[str, str] | None = None) -> bool:
    """真实浏览器路径必须由操作者显式开启。"""
    env = os.environ if environ is None else environ
    return str(env.get(REAL_RUN_ENV, "")).strip().lower() in {"1", "true", "yes"}


def _evidence_dir() -> Path:
    configured = str(os.environ.get("GW_C12_EVIDENCE_DIR", "")).strip()
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


def _timeline_fixture() -> dict[str, Any]:
    """固定分镜清单；它只提供静态图引用，不代表 Provider 生成成功。"""
    return {
        "tracks": [
            {
                "track_id": "video-01",
                "kind": "video",
                "clips": [
                    {
                        "shot_id": "SC01_SH001",
                        "asset_id": "asset-c12-t5-001",
                        "at": 0.0,
                        "duration": 0.25,
                        "overrides": {
                            "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='180'%3E%3Crect width='320' height='180' fill='%23141518'/%3E%3C/svg%3E#t=0,0.25",
                            "alt": "SC01_SH001",
                            "camera": "MS",
                            "movement": "DOLLY_IN",
                        },
                    },
                    {
                        "shot_id": "SC01_SH002",
                        "asset_id": "asset-c12-t5-002",
                        "at": 0.25,
                        "duration": 0.25,
                        "overrides": {
                            "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='180'%3E%3Crect width='320' height='180' fill='%232a1f12'/%3E%3C/svg%3E#t=0,0.25",
                            "alt": "SC01_SH002",
                            "camera": "CU",
                            "movement": "PAN_RIGHT",
                        },
                    },
                ],
            }
        ]
    }


def _manifest_contract(payload: dict[str, Any]) -> dict[str, Any]:
    clips = payload["tracks"][0]["clips"]
    assert [clip["shot_id"] for clip in clips] == ["SC01_SH001", "SC01_SH002"]
    assert [clip["at"] for clip in clips] == [0.0, 0.25]
    assert all(clip["asset_id"].startswith("asset-c12-t5-") for clip in clips)
    return {
        "fixture_id": FIXTURE_ID,
        "clip_count": len(clips),
        "shot_ids": [clip["shot_id"] for clip in clips],
        "asset_ids": [clip["asset_id"] for clip in clips],
        "durations": [clip["duration"] for clip in clips],
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
            body = json.loads(response.read(4096).decode("utf-8"))
        return response.status == 200 and body.get("status") == "ok"
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


class _OwnedServer:
    """只启动本测试拥有的隔离服务，支持停止后用同一数据目录重启。"""

    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = tmp_path / "c12-t5-server.log"
        self.env = os.environ.copy()
        self.env.update(
            {
                "GW_RUNTIME_MODE": "test",
                "GW_AUTH_MODE": "local_account",
                "GW_DATA_DIR": str(tmp_path / "data"),
                "GW_LOCAL_AUTH_DB": str(tmp_path / "auth" / "auth.sqlite3"),
                "GW_VIDEO_DATA_DIR": str(tmp_path / "video"),
                "GW_PORT": str(self.port),
                "GW_HOST": "127.0.0.1",
                "GW_RELOAD": "false",
                "PYTHONPATH": str(REPO_ROOT),
            }
        )
        for name in (
            "GW_PROVIDER_RUNTIME_JSON",
            "GW_CHAT_API_KEY",
            "GW_CHAT_BASE_URL",
            "GW_CLI_EXECUTION",
        ):
            self.env.pop(name, None)
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(
                [sys.executable, str(REPO_ROOT / "run.py"), "--test"],
                cwd=REPO_ROOT,
                env=self.env,
                stdout=log,
                stderr=log,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AssertionError(
                        f"C12-T5 测试服务提前退出，日志见 {self.log_path}"
                    )
                if _health_ready(self.port):
                    return
                time.sleep(0.1)
            raise AssertionError(f"C12-T5 测试服务未就绪，日志见 {self.log_path}")
        except BaseException:
            self.stop()
            raise
        finally:
            log.close()

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _same_origin(route: Any, base: str) -> None:
    if route.request.url.startswith(base + "/") or route.request.url.startswith(
        "data:"
    ):
        route.continue_()
    else:
        route.abort()


def _json_response(response: Any, expected: int) -> dict[str, Any]:
    assert response.status == expected, response.text()
    body = response.json() if response.text() else {}
    assert isinstance(body, dict)
    return body


def _dependency_snapshot(request: Any, base: str) -> dict[str, Any]:
    openapi = request.get(f"{base}/openapi.json", headers={"Origin": base})
    paths = openapi.json().get("paths", {}) if openapi.status == 200 else {}
    api_ready = any("timeline-manifest" in str(path) for path in paths)
    storyboard = request.get(
        f"{base}/static/pages/storyboard.html", headers={"Origin": base}
    )
    production = request.get(
        f"{base}/static/pages/production.html", headers={"Origin": base}
    )
    storyboard_html = storyboard.text() if storyboard.status == 200 else ""
    production_html = production.text() if production.status == 200 else ""
    return {
        "api_timeline_manifest": api_ready,
        "storyboard_sync_control": 'id="btnSyncTimeline"' in storyboard_html,
        "production_play_control": 'id="timelinePlayToggle"' in production_html,
        "openapi_status": openapi.status,
        "storyboard_status": storyboard.status,
        "production_status": production.status,
    }


def test_c12_real_browser_gate_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REAL_RUN_ENV, raising=False)
    assert _real_run_enabled() is False
    assert _real_run_enabled({REAL_RUN_ENV: "true"}) is True


def test_c12_timeline_fixture_is_explicitly_non_provider_evidence() -> None:
    payload = _timeline_fixture()
    contract = _manifest_contract(payload)
    serialized = json.dumps({"fixture": contract, "provider": None}, ensure_ascii=False)
    assert contract["fixture_id"] == FIXTURE_ID
    assert "provider_success" not in serialized
    assert "真实 Provider 已完成" not in serialized


def test_c12_timeline_rough_cut_real_browser_refresh_and_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C12-AC5：验证时间线写入、Chrome 连播、刷新与服务重启回读。"""
    if not _real_run_enabled():
        pytest.skip("C12 真实浏览器默认关闭；设置 GW_C12_REAL_E2E=1 后才会运行")
    browser_api = pytest.importorskip("playwright.sync_api")

    server = _OwnedServer(tmp_path)
    server.start()
    evidence = {
        "task": "QIN-214 / C12-T5",
        "commit_sha": _git_sha(),
        "fixture_id": FIXTURE_ID,
        "provider": None,
        "video_compute": False,
        "qualification": "pending_user_confirmation",
        "status": "not_run",
        "screenshots": [],
        "page_errors": [],
    }

    try:
        with open_playwright(browser_api) as playwright:
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
            except browser_api.Error as exc:
                evidence.update(
                    status="blocked_environment",
                    blocker=f"Chrome 不可启动：{type(exc).__name__}",
                )
                _write_json(_evidence_dir() / "c12-t5-dependency-gate.json", evidence)
                pytest.skip(f"Chrome 不可启动：{type(exc).__name__}")

            try:
                context = browser.new_context(viewport={"width": 1800, "height": 1000})
                context.route("**/*", lambda route: _same_origin(route, server.base))
                request = context.request
                _json_response(
                    request.post(
                        f"{server.base}/api/asset-auth/local/setup",
                        data={"username": "c12_t5_owner", "password": "Test2026"},
                        headers={"Origin": server.base},
                    ),
                    201,
                )

                dependencies = _dependency_snapshot(request, server.base)
                evidence["dependencies"] = dependencies
                if not all(
                    dependencies.get(key)
                    for key in (
                        "api_timeline_manifest",
                        "storyboard_sync_control",
                        "production_play_control",
                    )
                ):
                    evidence.update(
                        status="blocked_dependency",
                        blocker="C12-T3/T4 尚未合入当前 master；未执行伪造的连播验收",
                    )
                    _write_json(
                        _evidence_dir() / "c12-t5-dependency-gate.json", evidence
                    )
                    pytest.skip("当前 master 缺少 QIN-209/QIN-213 时间线依赖")

                project = _json_response(
                    request.post(
                        f"{server.base}/api/asset-registry/projects",
                        data={
                            "name": "C12 T5 timeline rough cut",
                            "project_type": "film",
                        },
                        headers={"Origin": server.base},
                    ),
                    201,
                )["project"]
                project_id = project["project_id"]
                pipeline = _json_response(
                    request.post(
                        f"{server.base}/api/episode-pipelines",
                        data={
                            "project_id": project_id,
                            "title": "C12 T5 rough cut fixture",
                            "seed": FIXTURE_ID,
                            "production_context": {
                                "episodes": [
                                    {
                                        "episode_id": "EP01",
                                        "code": "EP01",
                                        "name": "C12 T5",
                                        "scenes": [
                                            {
                                                "scene_id": "SC01",
                                                "code": "SC01",
                                                "name": "Timeline",
                                                "shots": [],
                                            }
                                        ],
                                    }
                                ]
                            },
                        },
                        headers={"Origin": server.base},
                    ),
                    201,
                )
                pipeline_id = pipeline["pipeline_id"]
                manifest_payload = _timeline_fixture()
                fixture_contract = _manifest_contract(manifest_payload)
                created = _json_response(
                    request.post(
                        f"{server.base}/api/episode-pipelines/{pipeline_id}/timeline-manifest",
                        data=manifest_payload,
                        headers={"Origin": server.base},
                    ),
                    201,
                )
                manifest = created["timeline_manifest"]
                assert manifest["version"] == 1
                assert [
                    clip["shot_id"] for clip in manifest["tracks"][0]["clips"]
                ] == fixture_contract["shot_ids"]
                _write_json(
                    _evidence_dir() / "c12-t5-timeline-api.json",
                    {
                        "project": project,
                        "pipeline": pipeline,
                        "fixture": fixture_contract,
                        "create": created,
                    },
                )

                page_errors: list[str] = []
                storyboard_page = context.new_page()
                storyboard_page.on(
                    "pageerror", lambda error: page_errors.append(str(error))
                )
                storyboard_page.goto(
                    f"{server.base}/static/pages/storyboard.html?project_id={project_id}&view=storyboard",
                    wait_until="networkidle",
                )
                storyboard_page.wait_for_function(
                    "!window.GWAuthGate || window.GWAuthGate.loaded === true"
                )
                storyboard_page.locator("#btnSyncTimeline").wait_for(state="visible")
                storyboard_page.screenshot(
                    path=str(_evidence_dir() / "c12-t5-storyboard.png"), full_page=True
                )
                evidence["screenshots"].append("c12-t5-storyboard.png")

                production_page = context.new_page()
                production_page.on(
                    "pageerror", lambda error: page_errors.append(str(error))
                )
                production_url = f"{server.base}/static/pages/production.html?project_id={project_id}"
                production_page.goto(production_url, wait_until="networkidle")
                production_page.wait_for_function(
                    "!window.GWAuthGate || window.GWAuthGate.loaded === true"
                )
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction?.getState?.().timelineClips?.length === 2",
                    timeout=30_000,
                )
                state = production_page.evaluate(
                    "() => window.WorkbenchProduction.getState()"
                )
                assert (
                    state["timelineManifest"]["manifest_id"] == manifest["manifest_id"]
                )
                assert [
                    clip["shot_id"] for clip in state["timelineClips"]
                ] == fixture_contract["shot_ids"]
                production_page.screenshot(
                    path=str(_evidence_dir() / "c12-t5-rough-cut-start.png"),
                    full_page=True,
                )
                evidence["screenshots"].append("c12-t5-rough-cut-start.png")

                production_page.evaluate(
                    "() => window.WorkbenchProduction.playTimeline()"
                )
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction.getState().timelinePlaying === true",
                    timeout=5_000,
                )
                production_page.screenshot(
                    path=str(_evidence_dir() / "c12-t5-rough-cut-playing.png"),
                    full_page=True,
                )
                evidence["screenshots"].append("c12-t5-rough-cut-playing.png")
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction.getState().timelinePlaying === false && window.WorkbenchProduction.getState().activeTimelineIndex === 1",
                    timeout=10_000,
                )
                evidence["playback"] = production_page.evaluate(
                    "() => window.WorkbenchProduction.getState()"
                )

                production_page.reload(wait_until="networkidle")
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction?.getState?.().timelineClips?.length === 2",
                    timeout=30_000,
                )
                refreshed = production_page.evaluate(
                    "() => window.WorkbenchProduction.getState()"
                )
                assert (
                    refreshed["timelineManifest"]["manifest_id"]
                    == manifest["manifest_id"]
                )
                assert [
                    clip["asset_id"] for clip in refreshed["timelineClips"]
                ] == fixture_contract["asset_ids"]
                production_page.screenshot(
                    path=str(_evidence_dir() / "c12-t5-after-refresh.png"),
                    full_page=True,
                )
                evidence["screenshots"].append("c12-t5-after-refresh.png")

                server.stop()
                server.start()
                production_page.reload(wait_until="networkidle")
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction?.getState?.().timelineClips?.length === 2",
                    timeout=30_000,
                )
                restarted = production_page.evaluate(
                    "() => window.WorkbenchProduction.getState()"
                )
                assert (
                    restarted["timelineManifest"]["manifest_id"]
                    == manifest["manifest_id"]
                )
                assert [
                    clip["shot_id"] for clip in restarted["timelineClips"]
                ] == fixture_contract["shot_ids"]
                production_page.screenshot(
                    path=str(_evidence_dir() / "c12-t5-after-restart.png"),
                    full_page=True,
                )
                evidence["screenshots"].append("c12-t5-after-restart.png")
                evidence.update(
                    status="observed",
                    project_id=project_id,
                    pipeline_id=pipeline_id,
                    manifest_id=manifest["manifest_id"],
                )
                evidence["page_errors"] = page_errors
                assert not page_errors, page_errors
                _write_json(_evidence_dir() / "c12-t5-report.json", evidence)
            finally:
                context.close()
                browser.close()
    finally:
        server.stop()
